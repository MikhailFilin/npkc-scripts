"""
ai_using_up.py  →  dwh_test_db.ai_using_studies  (Target CH)

Анализ использования ИИ врачами при описании КТ, МРТ, РГ, ММГ в НПКЦ.
Фаза 1 (VPN ON):  extract_phase()  — запросы к datalake, результат в _buffer
Фаза 2 (VPN OFF): load_phase()     — вставка из _buffer в dwh_test_db

Интегрируется в all_dashboards_up.py.
Глубина: DAYS_TO_SYNC (по умолчанию 3, задаётся из all_dashboards_up).
"""

import sys
import subprocess
import time
import traceback
import numpy as np
import pandas as pd
import pyautogui
import clickhouse_connect
from datetime import date, timedelta

sys.path.insert(0, r'C:\Users\risen\Desktop\MySecondBrain\project\scripts')
import personal_config as cfg
from ntfy_notifier import send_ntfy_alert

# Задаётся из all_dashboards_up.py
DAYS_TO_SYNC = 30

MU_ID = '100001912827'

MODALITY_CONFIG = {
    'КТ':  {'device_type': 'КТ',  'ai_log_modalities': ('CT', 'CT_ASMT')},
    'МРТ': {'device_type': 'МРТ', 'ai_log_modalities': ('MR', 'MR_ASMT')},
    # У РГ/ММГ нет отдельного суффикса *_ASMT — ИИ-серия в kis.v_ai_log
    # помечена тем же generic modality='ASMT', что и обычный просмотр
    'РГ':  {'device_type': 'РГ',  'ai_log_modalities': ('ASMT', 'CR', 'DX')},
    'ММГ': {'device_type': 'ММГ', 'ai_log_modalities': ('ASMT', 'MG')},
}

# Буфер между фазами
_buffer: pd.DataFrame | None = None


# ===========================================================================
# VPN (подменяются заглушками в all_dashboards_up.py)
# ===========================================================================
VPN_APP_PATH            = cfg.VPN_APP_PATH
VPN_PASSWORD            = cfg.VPN_PASSWORD
PASSWORD_FIELD_X,       PASSWORD_FIELD_Y       = cfg.PASSWORD_FIELD_X,       cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X,       CONNECT_BUTTON_Y       = cfg.CONNECT_BUTTON_X,       cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X,     RIGHT_CLICK_MENU_Y     = cfg.RIGHT_CLICK_MENU_X,     cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X,   CONFIRMATION_CLICK_Y   = cfg.CONFIRMATION_CLICK_X,   cfg.CONFIRMATION_CLICK_Y


def connect_vpn():
    print('[ai_using] Запускаю TrGUI...')
    send_ntfy_alert('Запускаю VPN для ai_using...', title='VPN Connect', priority='default', tags='lock')
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f'   PID: {process.pid}')
    except Exception as e:
        msg = f'Ошибка запуска VPN: {e}'
        print(msg)
        send_ntfy_alert(msg, title='VPN Error', priority='urgent', tags='warning')
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
    print('[ai_using] VPN подключён.')
    send_ntfy_alert('VPN подключён', title='VPN Connected', priority='high', tags='key')


def disconnect_vpn():
    print('[ai_using] Отключаю VPN...')
    send_ntfy_alert('Отключаюсь от VPN...', title='VPN Disconnect', priority='default', tags='unlock')
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print('[ai_using] VPN отключён.')
    send_ntfy_alert('VPN отключён', title='VPN Disconnected', priority='default', tags='check')


# ===========================================================================
# Подключения
# ===========================================================================
def _source_client():
    return clickhouse_connect.get_client(
        host=cfg.CH_HOST, port=cfg.CH_PORT,
        username=cfg.CH_USER, password=cfg.CH_PASSWORD,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=900,
    )

def _target_client():
    return clickhouse_connect.get_client(
        host=cfg.CH_HOST_TARGET, port=cfg.CH_PORT_TARGET,
        username=cfg.CH_USER_TARGET, password=cfg.CH_PASSWORD_TARGET,
        secure=True, verify=False,
        connect_timeout=15, send_receive_timeout=600,
    )


# ===========================================================================
# SQL-анализ одной модальности
# ===========================================================================
def _analyse_one(client, date_from: str, date_to: str, modality: str) -> pd.DataFrame:
    conf = MODALITY_CONFIG[modality]
    device_type   = conf['device_type']
    ai_modalities = ", ".join(f"'{m}'" for m in conf['ai_log_modalities'])

    sql = f"""
        WITH
        base_study AS (
            SELECT
                study_uid,
                argMin(accession_number, assignment_describe_start_date)          AS accession_number,
                argMin(assignment_result_emp_fio, assignment_describe_start_date)  AS doctor_fio,
                min(assignment_describe_start_date)                                AS describe_start,
                ifNull(min(assignment_result_doc_created_date),
                       toDateTime('2099-01-01 00:00:00'))                          AS conclusion_time,
                argMin(diagnostic_name, assignment_describe_start_date)            AS diagnostic_name
            FROM data_views.v_eris_assignment_results
            WHERE assignment_describe_mu_id = '{MU_ID}'
              AND device_type = '{device_type}'
              -- период фильтруется по дате подписания заключения (как в concl_npkc_up.py),
              -- а не по дате начала описания — иначе популяция исследований расходится
              AND toDate(assignment_result_doc_created_date) BETWEEN '{date_from}' AND '{date_to}'
            GROUP BY study_uid
        ),
        ai_result AS (
            SELECT
                JSONExtractString(raw_data, 'studyIUID')               AS study_uid,
                any(JSONExtractString(raw_data, 'aiResult', 'norma'))  AS norma_value
            FROM dwh_views.v_eris_report
            WHERE app_source = 'CDS'
              AND parseDateTimeBestEffortOrNull(
                      JSONExtractString(computed_data, 'pumStudyReadyForAiTime')
                  ) IS NOT NULL
              AND notEmpty(trim(JSONExtractString(raw_data, 'aiResult', 'norma')))
            GROUP BY study_uid
        ),
        ai_log_msk AS (
            SELECT
                l.study_iuid,
                l.opened_at + INTERVAL 3 HOUR AS ts_msk,
                l.access_method,
                l.procedure_name,
                b.describe_start,
                b.conclusion_time
            FROM kis.v_ai_log AS l
            INNER JOIN base_study AS b ON l.study_iuid = b.study_uid
            WHERE l.modality IN ({ai_modalities})
              AND toDate(l.opened_at)
                  BETWEEN '{date_from}'::Date - INTERVAL 7 DAY
                      AND '{date_to}'::Date   + INTERVAL 30 DAY
        ),
        ai_actions AS (
            SELECT
                study_iuid,
                minIf(ts_msk, ts_msk >= describe_start - INTERVAL 5 MINUTE
                              AND ts_msk <= conclusion_time)                   AS first_ai_open,
                -- сгруппированные метрики (оставлены для обратной совместимости с дашбордами)
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND access_method IN ('MANUAL_LOAD', 'SCROLL'))         AS active_in_window,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name IN ('AI_VIEW_START', 'AI_VIEW_END')) AS view_in_window,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_VIEW_ENLARGE')                 AS enlarge_in_window,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND access_method IN ('AUTO_LOAD', 'AUTO_GRID'))        AS passive_in_window,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND access_method IN ('MANUAL_LOAD', 'SCROLL'))         AS outside_active_cnt,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name IN ('AI_VIEW_START', 'AI_VIEW_END', 'AI_VIEW_ENLARGE'))
                                                                                 AS outside_view_cnt,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND access_method IN ('AUTO_LOAD', 'AUTO_GRID'))        AS outside_passive_cnt,
                countIf(ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                                                                                 AS actions_outside,
                -- детализация по каждому procedure_name отдельно (во время описания)
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_AUTO_LOAD')                    AS cnt_auto_load,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_AUTO_GRID')                    AS cnt_auto_grid,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_MANUAL_LOAD')                  AS cnt_manual_load,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_VIEW_START')                   AS cnt_view_start,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_VIEW_END')                     AS cnt_view_end,
                countIf((ts_msk >= describe_start - INTERVAL 5 MINUTE AND ts_msk <= conclusion_time)
                        AND procedure_name = 'AI_VIEW_ENLARGE')                 AS cnt_enlarge,
                -- детализация по каждому procedure_name отдельно (вне окна описания)
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name = 'AI_AUTO_LOAD')                    AS outside_auto_load,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name = 'AI_AUTO_GRID')                    AS outside_auto_grid,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name = 'AI_MANUAL_LOAD')                  AS outside_manual_load,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name = 'AI_VIEW_START')                   AS outside_view_start,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name = 'AI_VIEW_END')                     AS outside_view_end,
                countIf((ts_msk < describe_start - INTERVAL 5 MINUTE OR ts_msk > conclusion_time)
                        AND procedure_name = 'AI_VIEW_ENLARGE')                 AS outside_enlarge,
                arrayStringConcat(groupArray(DISTINCT access_method), ', ')    AS methods_used
            FROM ai_log_msk
            GROUP BY study_iuid
        )
        SELECT
            '{modality}'                                                                  AS modality,
            b.study_uid                                                                   AS study_uid,
            b.accession_number,
            b.doctor_fio,
            b.diagnostic_name,
            b.describe_start,
            if(b.conclusion_time = toDateTime('2099-01-01 00:00:00'), NULL,
               b.conclusion_time)                                                         AS conclusion_time,
            if(r.study_uid IS NOT NULL, 1, 0)                                            AS has_ai,
            r.norma_value,
            CASE
                WHEN a.study_iuid IS NULL
                  OR (a.active_in_window + a.view_in_window
                      + a.enlarge_in_window + a.passive_in_window) = 0 THEN 'no_ai'
                WHEN (a.active_in_window + a.view_in_window + a.enlarge_in_window) > 0   THEN 'active'
                ELSE 'passive'
            END                                                                           AS ai_interaction,
            nullIf(a.first_ai_open, toDateTime('1970-01-01 00:00:00'))                   AS first_ai_open,
            if(a.first_ai_open > toDateTime('1970-01-01 00:00:00'),
               round(dateDiff('minute', b.describe_start, a.first_ai_open), 0),
               NULL)                                                                      AS mins_to_first_ai,
            if(a.first_ai_open > toDateTime('1970-01-01 00:00:00')
               AND a.first_ai_open < b.conclusion_time, 1, 0)                            AS ai_before_conclusion,
            a.active_in_window,
            a.view_in_window,
            a.enlarge_in_window,
            a.passive_in_window,
            CASE
                WHEN a.study_iuid IS NULL                              THEN 'none'
                WHEN (a.outside_active_cnt + a.outside_view_cnt) > 0  THEN 'active'
                WHEN a.outside_passive_cnt > 0                         THEN 'passive'
                ELSE 'none'
            END                                                                           AS outside_ai_interaction,
            a.outside_active_cnt,
            a.outside_view_cnt,
            a.outside_passive_cnt,
            if(a.actions_outside > 0, 1, 0)                                              AS has_action_outside,
            a.methods_used,
            a.cnt_auto_load,
            a.cnt_auto_grid,
            a.cnt_manual_load,
            a.cnt_view_start,
            a.cnt_view_end,
            a.cnt_enlarge,
            a.outside_auto_load,
            a.outside_auto_grid,
            a.outside_manual_load,
            a.outside_view_start,
            a.outside_view_end,
            a.outside_enlarge
        FROM base_study AS b
        LEFT JOIN ai_result  AS r ON b.study_uid = r.study_uid
        LEFT JOIN ai_actions AS a ON b.study_uid = a.study_iuid
        ORDER BY b.describe_start
    """
    print(f'  [{modality}] запрос за {date_from} — {date_to}...')
    df = client.query_df(sql)
    print(f'  [{modality}] получено {len(df)} строк')
    return df


# ===========================================================================
# ФАЗА 1: извлечение (VPN ON, datalake)
# ===========================================================================
def extract_phase() -> bool:
    global _buffer
    today     = date.today()
    date_to   = today - timedelta(days=1)
    date_from = date_to - timedelta(days=DAYS_TO_SYNC - 1)
    d_from, d_to = str(date_from), str(date_to)

    print(f'\n[ai_using] ФАЗА 1 — период {d_from} — {d_to}')
    try:
        client = _source_client()
        frames = [_analyse_one(client, d_from, d_to, mod) for mod in MODALITY_CONFIG]
        _buffer = pd.concat(frames, ignore_index=True)
        _buffer.attrs['date_from'] = d_from
        _buffer.attrs['date_to']   = d_to
        total = len(_buffer)
        print(f'[ai_using] извлечено: {total} строк ({" + ".join(MODALITY_CONFIG)})')
        return total > 0
    except Exception as e:
        print(f'[ai_using] ❌ extract_phase: {e}')
        return False


# ===========================================================================
# ФАЗА 2: загрузка (VPN OFF, target CH)
# ===========================================================================
def _prepare(df: pd.DataFrame, date_from: str, date_to: str) -> pd.DataFrame:
    upload = df.copy()

    # strip timezone
    for col in upload.select_dtypes(include=['datetimetz']).columns:
        upload[col] = upload[col].dt.tz_localize(None)

    upload['period_from'] = date.fromisoformat(date_from)
    upload['period_to']   = date.fromisoformat(date_to)

    col_order = [
        'modality', 'study_uid', 'accession_number', 'doctor_fio', 'diagnostic_name',
        'describe_start', 'conclusion_time',
        'has_ai', 'norma_value', 'ai_interaction',
        'first_ai_open', 'mins_to_first_ai', 'ai_before_conclusion',
        'active_in_window', 'view_in_window', 'enlarge_in_window', 'passive_in_window',
        'outside_ai_interaction', 'outside_active_cnt', 'outside_view_cnt',
        'outside_passive_cnt', 'has_action_outside', 'methods_used',
        'cnt_auto_load', 'cnt_auto_grid', 'cnt_manual_load',
        'cnt_view_start', 'cnt_view_end', 'cnt_enlarge',
        'outside_auto_load', 'outside_auto_grid', 'outside_manual_load',
        'outside_view_start', 'outside_view_end', 'outside_enlarge',
        'period_from', 'period_to',
    ]
    upload = upload[col_order]

    # строки без study_uid — битые данные источника
    bad = upload['study_uid'].isna().sum()
    if bad:
        print(f'  Пропущено {bad} строк с пустым study_uid')
        upload = upload.dropna(subset=['study_uid'])

    # дубли по study_uid внутри одной выгрузки (не должно быть из-за GROUP BY в SQL,
    # но если study попал в обе модальности — оставляем самую позднюю по describe_start)
    dup = upload['study_uid'].duplicated().sum()
    if dup:
        print(f'  Удалено {dup} дублей study_uid внутри выгрузки')
        upload = upload.sort_values('describe_start').drop_duplicates('study_uid', keep='last')

    # norma_value: float/str → '0'/'1'/None
    upload['norma_value'] = upload['norma_value'].apply(
        lambda x: str(int(float(x))) if pd.notna(x) and x != '' else None
    )

    for col in ['has_ai', 'ai_before_conclusion', 'has_action_outside']:
        upload[col] = pd.to_numeric(upload[col], errors='coerce').fillna(0).astype(int)

    for col in ['active_in_window', 'view_in_window', 'enlarge_in_window', 'passive_in_window',
                'outside_active_cnt', 'outside_view_cnt', 'outside_passive_cnt',
                'cnt_auto_load', 'cnt_auto_grid', 'cnt_manual_load',
                'cnt_view_start', 'cnt_view_end', 'cnt_enlarge',
                'outside_auto_load', 'outside_auto_grid', 'outside_manual_load',
                'outside_view_start', 'outside_view_end', 'outside_enlarge']:
        upload[col] = upload[col].apply(lambda x: int(x) if pd.notna(x) else None)

    upload['mins_to_first_ai'] = upload['mins_to_first_ai'].apply(
        lambda x: float(x) if pd.notna(x) else None
    )

    def _clean(v):
        if v is None:
            return None
        if isinstance(v, float) and np.isnan(v):
            return None
        return v

    for col in upload.select_dtypes(include=['object']).columns:
        upload[col] = upload[col].apply(_clean)

    return upload


def _dedup_mutation(tc):
    """ALTER TABLE ... DELETE — оставляет по каждому study_uid только
    самую свежую версию (по loaded_at). Кидает исключение при ошибке."""
    tc.command(
        '''
        ALTER TABLE dwh_test_db.ai_using_studies
        DELETE WHERE (study_uid, loaded_at) NOT IN (
            SELECT study_uid, max(loaded_at)
            FROM dwh_test_db.ai_using_studies
            GROUP BY study_uid
        )
        ''',
        settings={'mutations_sync': '1'},
    )


def dedup_check(verbose: bool = True) -> bool:
    """Безопасная самостоятельная проверка/устранение дублей study_uid
    в dwh_test_db.ai_using_studies. Не зависит от _buffer — можно вызывать
    отдельно (в конце main() или из all_dashboards_up.py) как страховку
    на случай, если дедуп внутри load_phase() не отработал."""
    try:
        tc = _target_client()
        total = int(tc.query_df(
            "SELECT count() AS cnt FROM dwh_test_db.ai_using_studies"
        ).iloc[0, 0])
        uniq = int(tc.query_df(
            "SELECT count(DISTINCT study_uid) AS cnt FROM dwh_test_db.ai_using_studies"
        ).iloc[0, 0])
        dup = total - uniq

        if dup <= 0:
            if verbose:
                print(f'[ai_using] dedup_check: дублей нет ({total} строк)')
            return True

        if verbose:
            print(f'[ai_using] dedup_check: найдено {dup} дублей study_uid '
                  f'({total} строк, {uniq} уникальных) — устраняю...')
        _dedup_mutation(tc)

        total_after = int(tc.query_df(
            "SELECT count() AS cnt FROM dwh_test_db.ai_using_studies"
        ).iloc[0, 0])
        if verbose:
            print(f'[ai_using] dedup_check: удалено {total - total_after} строк, '
                  f'осталось {total_after}')
        return True
    except Exception as e:
        print(f'[ai_using] ❌ dedup_check: {e}')
        print(traceback.format_exc())
        send_ntfy_alert(
            f'ai_using dedup_check: ошибка — {e}',
            title='AI Using Dedup Check Failed', priority='urgent', tags='warning',
        )
        return False


def load_phase() -> bool:
    global _buffer
    if _buffer is None or len(_buffer) == 0:
        print('[ai_using] ⚠️ буфер пуст, пропускаю загрузку')
        return False

    date_from = _buffer.attrs.get('date_from', '')
    date_to   = _buffer.attrs.get('date_to', '')

    print(f'\n[ai_using] ФАЗА 2 — загрузка {len(_buffer)} строк в dwh_test_db.ai_using_studies')
    try:
        upload = _prepare(_buffer, date_from, date_to)
        tc = _target_client()
        tc.insert_df('dwh_test_db.ai_using_studies', upload)
    except Exception as e:
        print(f'[ai_using] ❌ load_phase (insert): {e}')
        print(traceback.format_exc())
        send_ntfy_alert(
            f'ai_using load_phase: ошибка вставки — {e}',
            title='AI Using Insert Failed', priority='urgent', tags='warning',
        )
        return False

    # принудительная дедупликация по study_uid. ReplacingMergeTree схлопывает
    # дубли только в рамках одного ORDER BY ключа (modality, toDate(describe_start),
    # study_uid) — если у исследования между двумя синхронизациями сдвинулся
    # describe_start (например, описание перезапустили на след. день), ключ
    # меняется и OPTIMIZE FINAL дубль не убирает. Поэтому удаляем явно по study_uid,
    # оставляя только самую свежую версию (по loaded_at).
    # Отдельный try — если дедуп упадёт (например, таймаут мутации), вставленные
    # данные уже не теряем и не гоняем повторно, но явно сообщаем об ошибке.
    print('[ai_using] дедупликация study_uid (ALTER TABLE ... DELETE)...')
    try:
        _dedup_mutation(tc)
    except Exception as e:
        print(f'[ai_using] ❌ дедупликация не выполнена: {e}')
        print(traceback.format_exc())
        send_ntfy_alert(
            f'ai_using load_phase: дедупликация study_uid упала — {e}',
            title='AI Using Dedup Failed', priority='urgent', tags='warning',
        )
        # вставка прошла успешно — буфер очищаем, дубли почистятся при следующем запуске
        _buffer = None
        return True

    try:
        cnt = tc.query_df(
            f"SELECT count() AS cnt FROM dwh_test_db.ai_using_studies "
            f"WHERE period_from = '{date_from}' AND period_to = '{date_to}'"
        )
        total_all = tc.query_df(
            "SELECT count() AS cnt FROM dwh_test_db.ai_using_studies"
        )
        print(f'[ai_using] ✅ загружено за период: {cnt.iloc[0,0]} | всего в таблице: {total_all.iloc[0,0]}')
    except Exception as e:
        print(f'[ai_using] ⚠️ не удалось получить итоговые счётчики: {e}')
        print(traceback.format_exc())

    _buffer = None
    return True


# ===========================================================================
# Полный цикл (standalone): VPN → extract → VPN off → load
# ===========================================================================
def main():
    print(f'[ai_using] Запуск — последние {DAYS_TO_SYNC} дней')
    send_ntfy_alert(
        f'AI Using: синхронизация за {DAYS_TO_SYNC} дней...',
        title='AI Using Start', priority='default', tags='robot',
    )

    # Шаг 1: VPN ON → extract
    try:
        connect_vpn()
    except Exception as e:
        msg = f'[ai_using] Не удалось подключить VPN: {e}'
        print(msg)
        send_ntfy_alert(msg, title='AI Using Fatal', priority='urgent', tags='fire')
        sys.exit(1)

    ok = extract_phase()

    # Шаг 2: VPN OFF
    try:
        disconnect_vpn()
    except Exception as e:
        send_ntfy_alert(f'Ошибка отключения VPN: {e}', title='VPN Warning',
                        priority='default', tags='warning')

    if not ok:
        msg = f'[ai_using] Извлечение не удалось. Выход.'
        print(msg)
        send_ntfy_alert(msg, title='AI Using Failed', priority='urgent', tags='warning')
        sys.exit(1)

    # Шаг 3: load → target CH
    ok = load_phase()

    # Шаг 4: страховочная проверка дублей — всегда, независимо от ok
    dedup_check()

    if ok:
        send_ntfy_alert(
            f'AI Using: обновление ai_using_studies завершено ({DAYS_TO_SYNC} дней)!',
            title='AI Using Done', priority='high', tags='white_check_mark',
            topic_override='push_mrc_dashboards_7895',
        )
        print('[ai_using] Готово.')
    else:
        send_ntfy_alert(
            'AI Using: загрузка завершилась с ошибкой!',
            title='AI Using Failed', priority='urgent', tags='warning',
        )
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='AI using studies — загрузка в dwh_test_db')
    parser.add_argument('--days', type=int, default=DAYS_TO_SYNC,
                        help=f'Глубина анализа в днях (по умолчанию {DAYS_TO_SYNC})')
    args = parser.parse_args()
    DAYS_TO_SYNC = args.days
    main()
