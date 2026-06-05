import subprocess
import pyautogui
import time
import sys
import os
from datetime import datetime
import requests

# === Импорты ===
import clickhouse_connect
import pandas as pd
import pickle as _pickle

# === Импорт модуля уведомлений ===
from ntfy_notifier import send_ntfy_alert
import personal_config as cfg

# === Настройки исходной ClickHouse ===
CH_HOST = cfg.CH_HOST
CH_PORT = cfg.CH_PORT
CH_USER = cfg.CH_USER
CH_PASSWORD = cfg.CH_PASSWORD
CH_DATABASE = cfg.CH_DATABASE

# === Настройки целевой ClickHouse ===
CH_HOST_TARGET = cfg.CH_HOST_TARGET
CH_PORT_TARGET = cfg.CH_PORT_TARGET
CH_USER_TARGET = cfg.CH_USER_TARGET
CH_PASSWORD_TARGET = cfg.CH_PASSWORD_TARGET
CH_DATABASE_TARGET = cfg.CH_DATABASE_TARGET

CH_TABLE = 'ai_norma_comparing'

_BUFFER_РАСХОЖДЕНИЕ = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'расхождение_phase_buffer.pkl')

# === Настройки для VPN ===
VPN_APP_PATH = cfg.VPN_APP_PATH
VPN_PASSWORD = cfg.VPN_PASSWORD

# Координаты
PASSWORD_FIELD_X, PASSWORD_FIELD_Y = cfg.PASSWORD_FIELD_X, cfg.PASSWORD_FIELD_Y
CONNECT_BUTTON_X, CONNECT_BUTTON_Y = cfg.CONNECT_BUTTON_X, cfg.CONNECT_BUTTON_Y
RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y = cfg.RIGHT_CLICK_MENU_X, cfg.RIGHT_CLICK_MENU_Y
DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y = cfg.DISCONNECT_MENU_ITEM_X, cfg.DISCONNECT_MENU_ITEM_Y
CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y = cfg.CONFIRMATION_CLICK_X, cfg.CONFIRMATION_CLICK_Y


def send_log_ntfy_message(message: str, title: str = "AI Norma Log"):
    send_ntfy_alert(message=message, title=title, priority="default", tags="log")


def send_dashboard_ntfy_message(message: str, title: str = "Dashboard Update"):
    send_ntfy_alert(message=message, title=title, priority="high", tags="chart_with_upwards_trend")


def connect_vpn():
    print("🔄 Запускаю TrGUI...")
    send_log_ntfy_message("🔄 Запуск VPN клиента...")
    try:
        process = subprocess.Popen(VPN_APP_PATH)
        print(f"   PID: {process.pid}")
    except Exception as e:
        error_msg = f"❌ Ошибка запуска VPN: {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg, title="VPN Error")
        raise
    time.sleep(15)
    windows = pyautogui.getWindowsWithTitle('')
    activated = False
    for window in windows:
        if any(kw in window.title.lower() for kw in ['check point', 'trgui', 'endpoint']):
            try:
                window.activate()
                time.sleep(2)
                print(f"✅ Окно '{window.title}' активировано.")
                activated = True
                break
            except:
                pass
    if not activated:
        print("⚠️ Не удалось активировать окно.")
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
    print("✅ Подключение к VPN инициировано.")
    send_log_ntfy_message("✅ Подключение к VPN инициировано.")


def disconnect_vpn():
    print("🛑 Начинаю отключение...")
    send_log_ntfy_message("🛑 Начинаю отключение от VPN...")
    pyautogui.rightClick(RIGHT_CLICK_MENU_X, RIGHT_CLICK_MENU_Y)
    time.sleep(2)
    pyautogui.click(DISCONNECT_MENU_ITEM_X, DISCONNECT_MENU_ITEM_Y)
    time.sleep(3)
    pyautogui.click(CONFIRMATION_CLICK_X, CONFIRMATION_CLICK_Y)
    time.sleep(3)
    print("🛑 Отключение завершено.")
    send_log_ntfy_message("🛑 Отключение от VPN выполнено.")


def ensure_ch_table():
    """Создаёт таблицу в целевой ClickHouse, если не существует."""
    client = clickhouse_connect.get_client(
        host=CH_HOST_TARGET, port=CH_PORT_TARGET,
        username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
        database=CH_DATABASE_TARGET, secure=True, verify=False
    )
    client.command(f'''
    CREATE TABLE IF NOT EXISTS {CH_TABLE} (
        studyiuid                 String,
        kafkaaiqueueid            String,
        modelid                   Int32,
        pumstudyreadyforaitime    String,
        description               String,
        norma                     String,
        pathologyflag             UInt8,
        confidencelevel           Int32,
        modelversion              String,
        seriesiuid                String,
        defect_b                  Int8,
        pumreportlatesttime       String,
        time_diff_minutes         Float64,
        defect_a                  Int8,
        servis                    String,
        descr_result_doctor_fio   String,
        descr_result_description  String,
        descr_result_conclusion   String
    ) ENGINE = MergeTree()
    ORDER BY studyiuid
    ''')
    client.close()


def _to_int_safe(x, default=0):
    """None/NaN/bool/str → int, иначе default."""
    if x is None:
        return default
    try:
        if isinstance(x, float) and pd.isna(x):
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(x, bool):
        return int(x)
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def write_df_to_ch(df: pd.DataFrame) -> bool:
    """TRUNCATE + INSERT DataFrame в целевую ClickHouse."""
    try:
        ensure_ch_table()
        client = clickhouse_connect.get_client(
            host=CH_HOST_TARGET, port=CH_PORT_TARGET,
            username=CH_USER_TARGET, password=CH_PASSWORD_TARGET,
            database=CH_DATABASE_TARGET, secure=True, verify=False
        )
        client.command(f'TRUNCATE TABLE IF EXISTS {CH_TABLE}')
        df = df.copy()

        # seriesiuid: список → строка с запятыми (DBeaver не отображает Array через JDBC)
        df['seriesiuid'] = df['seriesiuid'].apply(
            lambda x: ','.join(x) if isinstance(x, list) else ''
        )

        # Числовые колонки — защита от None/NaN перед astype
        df['pathologyflag'] = df['pathologyflag'].apply(lambda x: _to_int_safe(x)).astype('uint8')
        df['defect_b'] = df['defect_b'].apply(lambda x: _to_int_safe(x)).astype('int8')
        df['defect_a'] = df['defect_a'].apply(lambda x: _to_int_safe(x)).astype('int8')
        df['modelid'] = df['modelid'].apply(lambda x: _to_int_safe(x)).astype('int32')
        df['confidencelevel'] = df['confidencelevel'].apply(lambda x: _to_int_safe(x)).astype('int32')
        df['time_diff_minutes'] = pd.to_numeric(df['time_diff_minutes'], errors='coerce').fillna(0.0)

        # String колонки — заменяем None/NaN на ''
        str_cols = ['studyiuid', 'kafkaaiqueueid', 'pumstudyreadyforaitime', 'description',
                    'norma', 'modelversion', 'pumreportlatesttime', 'servis',
                    'descr_result_doctor_fio', 'descr_result_description', 'descr_result_conclusion']
        for col in str_cols:
            df[col] = df[col].apply(lambda x: '' if x is None or (isinstance(x, float) and pd.isna(x)) else str(x))

        client.insert_df(CH_TABLE, df)
        client.close()
        return True
    except Exception as e:
        error_msg = f"❌ Ошибка записи в целевую ClickHouse: {e}"
        print(error_msg)
        send_log_ntfy_message(error_msg, title="CH Target Error")
        return False


def export_validation_ai_results():
    """Загрузка данных из source ClickHouse в целевую (ai_norma_comparing)."""
    print(f"📊 Загрузка данных в {CH_DATABASE_TARGET}.{CH_TABLE}...")
    send_log_ntfy_message(f"📊 Загрузка {CH_DATABASE_TARGET}.{CH_TABLE}...")

    try:
        connect_vpn()
    except Exception as e:
        send_log_ntfy_message(f"❌ Ошибка VPN: {e}", title="VPN Error")
        return False

    client = None
    df = None
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False,
            send_receive_timeout=2200,
            connect_timeout=60
        )
        print("✅ ClickHouse подключён.")

        query = """WITH prepared_data AS (
    SELECT
        if(raw_data = 'null',
            JSONExtractString(computed_data, 'studyUid'),
            JSONExtractString(raw_data, 'studyIUID')
        ) AS studyIUID,
        JSONExtractString(computed_data, 'taskId') AS taskId,
        JSONExtractString(computed_data, 'kafkaAiQueueId') AS kafkaAiQueueId,
        if(raw_data = 'null',
            JSONExtractInt(computed_data, 'modelId'),
            JSONExtractInt(raw_data, 'aiResult', 'modelId')
        ) AS modelId,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'downloadStartDT'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS downloadStartDT,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'downloadEndDT'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS downloadEndDT,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'processStartDT'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS processStartDT,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'processEndDT'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS processEndDT,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(computed_data, 'pumStudyReadyForAiTime'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS pumStudyReadyForAiTime,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(computed_data, 'pumStudyAcceptedTime'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS pumStudyAcceptedTime,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(computed_data, 'pumReportSentTime'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS pumReportSentTime,
        parseDateTimeBestEffortOrNull(
            replaceRegexpAll(
                JSONExtractString(computed_data, 'pumReportLatestDicomInstanceTime'),
                ':(60)(\\.\\d+)?',
                ':59.000'
            )
        ) AS pumReportLatestDicomInstanceTime,
        if(raw_data = 'null',
            'error',
            JSONExtractString(raw_data, 'aiResult', 'error')
        ) AS error,
        JSONExtractString(raw_data, 'aiResult', 'description') AS description,
        JSONExtractString(raw_data, 'aiResult', 'norma') AS norma,
        JSONExtractBool(raw_data, 'aiResult', 'pathologyFlag') AS pathologyFlag,
        JSONExtractInt(raw_data, 'aiResult', 'confidenceLevel') AS confidenceLevel,
        JSONExtractString(computed_data, 'modality') AS modality,
        JSONExtractString(computed_data, 'anatomicalAreaCode') AS anatomicalAreaCode,
        JSONExtractString(raw_data, 'aiResult', 'modelVersion') AS modelVersion,
        JSONExtractString(raw_data, 'aiResult', 'seriesIUID') AS seriesIUID,
        raw_data = 'null' AS is_raw_null
    FROM dwh_views.v_eris_report
    WHERE app_source = 'CDS'
    AND parseDateTimeBestEffortOrNull(JSONExtractString(computed_data, 'pumStudyReadyForAiTime'))
        >= '2025-10-01 00:00:00'),
aggregated AS (
    SELECT
        studyIUID,
        taskId,
        kafkaAiQueueId,
        modelId,
        min(downloadStartDT) AS downloadStartDT,
        max(downloadEndDT) AS downloadEndDT,
        min(processStartDT) AS processStartDT,
        max(processEndDT) AS processEndDT,
        min(pumStudyReadyForAiTime) AS pumStudyReadyForAiTime,
        max(pumStudyAcceptedTime) AS pumStudyAcceptedTime,
        max(pumReportSentTime) AS pumReportSentTime,
        max(pumReportLatestDicomInstanceTime) AS pumReportLatestDicomInstanceTime,
        arrayFirst(x -> x != '', groupUniqArray(error)) AS error,
        arrayFirst(x -> x != '', groupUniqArray(description)) AS description,
        arrayFirst(x -> x != '', groupUniqArray(norma)) AS norma,
        arrayFirst(x -> x != false, groupUniqArray(pathologyFlag)) AS pathologyFlag,
        arrayFirst(x -> x != 0, groupUniqArray(confidenceLevel)) AS confidenceLevel,
        arrayFirst(x -> x != '', groupUniqArray(modality)) AS modality,
        arrayFirst(x -> x != '', groupUniqArray(anatomicalAreaCode)) AS anatomicalAreaCode,
        arrayFirst(x -> x != '', groupUniqArray(modelVersion)) AS modelVersion,
        arrayFilter(x -> x != '', groupUniqArray(seriesIUID)) AS seriesIUID,
        max(is_raw_null) AS is_raw_null
    FROM prepared_data
    GROUP BY studyIUID, taskId, kafkaAiQueueId, modelId
),
final_calculations AS (
    SELECT
        *,
        if(error != '' OR is_raw_null, 1, 0) AS "Дефект Б",
        greatest(pumReportSentTime, pumReportLatestDicomInstanceTime) AS pumReportLatestTime,
        dateDiff('second', pumStudyReadyForAiTime, greatest(pumReportSentTime, pumReportLatestDicomInstanceTime)) / 60.0 AS time_diff_minutes,
        CASE modelId
            WHEN 1002 THEN 'Care Mentor AI'
            WHEN 1003 THEN 'Третье мнение РГ'
            WHEN 1004 THEN 'Цельс ММГ'
            WHEN 1006 THEN 'FBM РГ ОГК'
            WHEN 1015 THEN 'Цельс ФЛГ'
            WHEN 1026 THEN 'IRYM MAMMOLens'
            WHEN 1027 THEN 'AIRadiology'
            WHEN 1040 THEN 'TrioDM (MTL)'
            WHEN 1054 THEN 'Care Mentor AI, FLATFOOT'
            WHEN 1059 THEN 'IMV MS'
            WHEN 1062 THEN 'Цельс РГ'
            WHEN 1063 THEN 'FBM ФЛГ'
            WHEN 1067 THEN 'Третье Мнение ФЛГ'
            WHEN 1068 THEN 'CVisionRad - Knee Arthrosis'
            WHEN 1075 THEN 'CVL - Chest CT Aorta'
            WHEN 1089 THEN 'АИ Диагностик МРТ ПКОП'
            WHEN 1090 THEN 'CVL - Abdomen CT Aorta'
            WHEN 1093 THEN 'Multivox ASPECTS CT ГМ'
            WHEN 1094 THEN 'Oxytech Spine XR Scoliosis'
            WHEN 1095 THEN 'Синапс Нейро. РГ Сколиоз'
            WHEN 1096 THEN 'ТретьеМнение.Маммограммы'
            WHEN 1099 THEN '.Цельс ОМС (ММГ)'
            WHEN 1100 THEN 'Chest-IRA'
            WHEN 1101 THEN 'Цельс КС КТ ОГК'
            WHEN 1102 THEN 'CVL - Chest CT Complex'
            WHEN 1120 THEN 'АрхиМед Aivory Chest X'
            WHEN 1123 THEN 'AIRadiology РГ синусит'
            WHEN 1125 THEN 'АИ Диагностик МРТ ГМ'
            WHEN 1128 THEN 'IMV GLIOMAS'
            WHEN 1129 THEN 'Sciberia Head'
            WHEN 1132 THEN 'АИ Диагностик Остеопороз Abd'
            WHEN 1134 THEN 'СберМед КТ ГМ ишемия'
            WHEN 1135 THEN 'АИ Диагностик Почки Abd'
            WHEN 1137 THEN 'СберМед КТ ГМ кровоизлияния'
            WHEN 1138 THEN 'VisionLabs Kidney Tumor Detector'
            WHEN 1139 THEN 'CVL - Abdomen CT Spine fracture'
            WHEN 1144 THEN 'IMV МРТ ПКОП'
            WHEN 1145 THEN 'АИ Диагностик КТ ОГК комплекс'
            WHEN 1147 THEN 'Esper.Scoliosis'
            WHEN 1148 THEN 'АрхиМед Aivory AI Nose X'
            WHEN 1149 THEN 'Oxytech РГ синусит'
            WHEN 1150 THEN '.Третье Мнение ММГ (ОМС)'
            WHEN 1151 THEN 'Abdomen-IRA'
            WHEN 1152 THEN 'АИ Диагностик образования надпочечников Abd'
            WHEN 1153 THEN 'АИ Диагностик Аорта Abd'
            WHEN 1155 THEN 'Третье мнение КТ ОГК комплекс'
            WHEN 1159 THEN 'Oxytech РГ коленного сустава'
            WHEN 1160 THEN 'А-рг-сколиоз'
            WHEN 1164 THEN 'Цельс КС КТ ГМ'
            WHEN 1165 THEN 'IMV PIRADS'
            WHEN 1166 THEN 'Oxytech Spine XR Spondylolisthesis'
            WHEN 1167 THEN 'NTechMed CT Brain Complex'
            WHEN 1168 THEN 'NtechMed MRI Brain'
            WHEN 1169 THEN 'АИ Диагностик КТ ГМ'
            WHEN 1172 THEN 'IMV МРТ ШОП'
            WHEN 1173 THEN 'АИ Диагностик мочекаменная болезнь Abd'
            WHEN 1174 THEN 'АИ Диагностик КТ ГМ морфометрия'
            WHEN 1175 THEN 'NTechMed Norm Brain CT'
            WHEN 1176 THEN 'NTechMed Norm Brain MRI'
            WHEN 1177 THEN 'А-рг-синусит'
            WHEN 1178 THEN 'Oxytech РГ перелом тазобедренного сустава'
            WHEN 1180 THEN 'Третье мнение КТ ГМ комплекс'
            WHEN 1181 THEN '.FBM ФЛГ (ОМС)'
            WHEN 1182 THEN '.FBM РГ (ОМС)'
            WHEN 1183 THEN 'СППР МРТ ПКОП'
            WHEN 1185 THEN '.Третье мнение ФЛГ (ОМС)'
            WHEN 1186 THEN '.Цельс ФЛГ (ОМС)'
            WHEN 1187 THEN '.Цельс РГ (ОМС)'
            WHEN 1188 THEN 'Cognitic-MRI-T'
            WHEN 1190 THEN '.Третье мнение РГ (ОМС)'
            WHEN 1192 THEN 'Cholelithiasis-IRA'
            WHEN 1196 THEN 'АИ Диагностик КТ ГМ комплекс'
            WHEN 1197 THEN 'АИ Диагностик КТ ОБП комплекс'
            WHEN 1198 THEN 'FBM PesPlanus'
            WHEN 1200 THEN 'Oxytech РГ перелом плечевого сустава'
            WHEN 1201 THEN 'Oxytech РГ плоскостопие'
            WHEN 1202 THEN 'Oxytech РГ перелом голеностопного сустава'
            WHEN 1203 THEN 'Oxytech РГ перелом лучезапястного сустава'
            WHEN 1204 THEN 'Oxytech КТ ОБП остеопороз'
            WHEN 1205 THEN 'IMV SUBCORTICAL'
            WHEN 1206 THEN 'IMV МРТ ГОП'
            WHEN 1207 THEN 'Третье мнение КТ ГМ морфометрия'
            WHEN 1208 THEN 'ЛОР КТ РГ синусит'
            WHEN 1209 THEN 'Oxytech РГ переломы позвоночника'
            WHEN 1210 THEN 'Цельс МРТ ОМТ'
            WHEN 1212 THEN 'СберМедИИ MoonShot'
            WHEN 1213 THEN 'Oxytech РГ костный возраст'
            WHEN 1214 THEN 'Oxytech КТ ОБП надпочечники'
            WHEN 1215 THEN 'Oxytech КТ ОБП аорта'
            WHEN 1216 THEN 'Oxytech КТ ОБП мочекаменная болезнь'
            WHEN 1217 THEN 'Oxytech КТ ОБП почки'
            WHEN 1218 THEN 'Oxytech РГ ОГК'
            WHEN 1219 THEN 'Oxytech ФЛГ'
            WHEN 1220 THEN 'Oxytech КТ ОБП печень'
            WHEN 1221 THEN 'Oxytech КТ ОБП желчнокаменная болезнь'
            WHEN 1226 THEN 'АрхиМед Aivory Pirads AI MR'
            WHEN 1227 THEN 'HealthSpine XR (сколиоз)'
            WHEN 1228 THEN 'HealthSpine XR (компрессионные переломы)'
            WHEN 1229 THEN 'Эирвэй РГ ТБС артроз'
            WHEN 1230 THEN 'Эирвэй РГ переломы тел позвонков'
            WHEN 1231 THEN 'IMV UTERUS'
            WHEN 1232 THEN 'Oxytech КТ ОБП морфометрия почек'
            WHEN 1233 THEN 'Oxytech КТ ОБП морфометрия печени'
            WHEN 1234 THEN 'Oxytech КТ ОБП морфометрия поджелудочной железы и селезенки'
            WHEN 1235 THEN 'Multivox Cerebrum'
            WHEN 1236 THEN 'RAZUM MEDX'
            WHEN 1237 THEN 'NtechMed MRI Brain GLIOMAS'
            WHEN 1238 THEN 'Эирвэй РГ перелом плечевого сустава'
            WHEN 1239 THEN 'Эирвэй РГ перелом лучезапястного сустава'
            WHEN 1240 THEN 'Эирвэй РГ перелом голеностопного сустава'
            WHEN 1241 THEN 'Эирвэй РГ ТБС перелом'
            WHEN 1242 THEN 'Эирвэй КТ ОБП. Образование печени'
            WHEN 1243 THEN 'Эирвэй КТ ОБП. Образование надпочечников'
            WHEN 1244 THEN 'Эирвэй КТ ОБП. Образование почек'
            WHEN 1245 THEN 'Эирвэй КТ ОБП. Мочекаменная болезнь'
            WHEN 1246 THEN 'Эирвэй КТ ОБП. Остеопороз'
            WHEN 1247 THEN 'Эирвэй КТ ОБП. Желчнокаменная болезнь'
            WHEN 1248 THEN 'Эирвэй КТ ОБП. Аневризма аорты'
            WHEN 1249 THEN 'ЛОР КТ РГ перелом лучезапястного сустава'
            WHEN 1250 THEN 'ЛОР КТ РГ перелом плечевого сустава'
            WHEN 1253 THEN '.TrioDM (MTL) ОМС'
            ELSE 'Неизвестный сервис'
        END AS "Сервис"
    FROM aggregated
),
best_vri AS (
    SELECT
        study_id,
        descr_result_doctor_fio,
        descr_result_description,
        descr_result_conclusion,
        descr_result_doctor_job_name,
        row_number() OVER (
            PARTITION BY study_id
            ORDER BY
                CASE
                    WHEN descr_result_conclusion != ''
                         AND descr_result_doctor_job_name != ''
                         AND descr_result_doctor_job_name != 'ERISPUM'
                         AND NOT startsWith(descr_result_doctor_fio, 'Не о')
                    THEN 0
                    ELSE 1
                END,
                CASE
                    WHEN descr_result_doctor_job_name = 'Врач-рентгенолог' THEN 0
                    ELSE 1
                END,
                rand()
        ) AS rn
    FROM data_views.v_route_instrumental
    WHERE
        study_id IS NOT NULL
        AND descr_result_conclusion != ''
        AND descr_result_doctor_job_name != ''
        AND descr_result_doctor_job_name != 'ERISPUM'
        AND NOT startsWith(descr_result_doctor_fio, 'Не о')
)
SELECT
    fc.studyIUID,
    fc.kafkaAiQueueId,
    fc.modelId,
    formatDateTime(fc.pumStudyReadyForAiTime, '%d.%m.%Y %H:%i:%S') AS pumStudyReadyForAiTime,
    fc.description,
    fc.norma,
    fc.pathologyFlag,
    fc.confidenceLevel,
    fc.modelVersion,
    fc.seriesIUID,
    fc."Дефект Б" AS defect_b,
    formatDateTime(fc.pumReportLatestTime, '%d.%m.%Y %H:%i:%S') AS pumReportLatestTime,
    fc.time_diff_minutes,
    if(fc.modelId IN (1150, 1099, 1062, 1063, 1067, 1096, 1181, 1182, 1185, 1186, 1187, 1188, 1190, 1253),
        if(fc.time_diff_minutes >= 3, 1, 0),
        if(fc.time_diff_minutes >= 6.5, 1, 0)
    ) AS defect_a,
    fc."Сервис" AS servis,
    vri.descr_result_doctor_fio,
    vri.descr_result_description,
    vri.descr_result_conclusion
FROM final_calculations fc
LEFT JOIN best_vri vri
    ON fc.studyIUID = vri.study_id::String
    AND vri.rn = 1
WHERE
    fc.norma IN (0)
    AND fc.modelId IN (1181, 1182)
        """

        print("📥 Выполняем сложный запрос к ClickHouse...")
        result = client.query(query)

        columns = ['studyiuid', 'kafkaaiqueueid', 'modelid', 'pumstudyreadyforaitime',
                   'description', 'norma', 'pathologyflag', 'confidencelevel', 'modelversion', 'seriesiuid',
                   'defect_b', 'pumreportlatesttime', 'time_diff_minutes', 'defect_a', 'servis',
                   'descr_result_doctor_fio', 'descr_result_description', 'descr_result_conclusion']

        df = pd.DataFrame(result.result_rows, columns=columns)
        print(f"📥 Получено {len(df)} строк данных.")

    except Exception as e:
        error_msg = f"❌ Ошибка при работе с ClickHouse: {type(e).__name__}: {str(e)[:2000]}"
        print(error_msg)
        send_log_ntfy_message(error_msg, title="CH Error")
        if client:
            client.close()
        disconnect_vpn()
        return False
    finally:
        if client:
            client.close()

    try:
        disconnect_vpn()
    except Exception as e:
        send_log_ntfy_message(f"⚠️ Ошибка отключения VPN: {e}", title="VPN Warning")

    if df is None or df.empty:
        print("📭 Нет данных для выгрузки.")
        send_log_ntfy_message(f"📭 Нет данных для выгрузки в {CH_TABLE}.")
        return True

    total_rows = len(df)
    print(f"📤 Начинаем выгрузку {total_rows} строк в {CH_DATABASE_TARGET}.{CH_TABLE}...")
    success = write_df_to_ch(df)
    if success:
        send_log_ntfy_message(f"✅ Успешно выгружено {total_rows} строк в {CH_TABLE}.", title="CH Success")
    return success


def main():
    script_name = "расхождение_ии_up.py"

    print(f"🚀 Запуск экспорта данных в {CH_DATABASE_TARGET}.{CH_TABLE}")
    send_log_ntfy_message(f"🚀 Запуск экспорта данных в {CH_TABLE}", title="Script Start")

    success = export_validation_ai_results()

    if success:
        print("🏁 Экспорт завершен успешно")
        send_log_ntfy_message("🏁 Экспорт данных (ai_norma_comparing) завершен успешно", title="Script Success")
        send_ntfy_alert(
            "✅ Обновление Расхождение ИИ завершено!",
            title="Dashbord Done",
            priority="high",
            tags="tada",
            topic_override="push_mrc_dashboards_7895"
        )
    else:
        print("❌ Экспорт завершен с ошибками")
        send_log_ntfy_message("❌ Экспорт данных (ai_norma_comparing) завершен с ошибками", title="Script Error")
        send_ntfy_alert(
            "❌ Расхождение ИИ завершился с ошибкой! Проверьте консоль.",
            title="Dashbord Failed",
            priority="urgent",
            tags="warning",
            topic_override="push_mrc_dashboards_7895"
        )

    return success


def extract_phase():
    """Фаза 1 (VPN включён): запрос к source ClickHouse → pickle-буфер."""
    print("📥 [расхождение] extract_phase: запрос к ClickHouse...")
    client = None
    try:
        client = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
            database=CH_DATABASE, secure=True, verify=False,
            send_receive_timeout=2200, connect_timeout=60
        )
        query = """WITH prepared_data AS (
    SELECT
        if(raw_data = 'null',
            JSONExtractString(computed_data, 'studyUid'),
            JSONExtractString(raw_data, 'studyIUID')
        ) AS studyIUID,
        JSONExtractString(computed_data, 'taskId') AS taskId,
        JSONExtractString(computed_data, 'kafkaAiQueueId') AS kafkaAiQueueId,
        if(raw_data = 'null',
            JSONExtractInt(computed_data, 'modelId'),
            JSONExtractInt(raw_data, 'aiResult', 'modelId')
        ) AS modelId,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'downloadStartDT'), ':(60)(\\.\\d+)?', ':59.000')) AS downloadStartDT,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'downloadEndDT'), ':(60)(\\.\\d+)?', ':59.000')) AS downloadEndDT,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'processStartDT'), ':(60)(\\.\\d+)?', ':59.000')) AS processStartDT,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(raw_data, 'aiResult', 'dateTimeParams', 'processEndDT'), ':(60)(\\.\\d+)?', ':59.000')) AS processEndDT,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(computed_data, 'pumStudyReadyForAiTime'), ':(60)(\\.\\d+)?', ':59.000')) AS pumStudyReadyForAiTime,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(computed_data, 'pumStudyAcceptedTime'), ':(60)(\\.\\d+)?', ':59.000')) AS pumStudyAcceptedTime,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(computed_data, 'pumReportSentTime'), ':(60)(\\.\\d+)?', ':59.000')) AS pumReportSentTime,
        parseDateTimeBestEffortOrNull(replaceRegexpAll(JSONExtractString(computed_data, 'pumReportLatestDicomInstanceTime'), ':(60)(\\.\\d+)?', ':59.000')) AS pumReportLatestDicomInstanceTime,
        if(raw_data = 'null', 'error', JSONExtractString(raw_data, 'aiResult', 'error')) AS error,
        JSONExtractString(raw_data, 'aiResult', 'description') AS description,
        JSONExtractString(raw_data, 'aiResult', 'norma') AS norma,
        JSONExtractBool(raw_data, 'aiResult', 'pathologyFlag') AS pathologyFlag,
        JSONExtractInt(raw_data, 'aiResult', 'confidenceLevel') AS confidenceLevel,
        JSONExtractString(computed_data, 'modality') AS modality,
        JSONExtractString(computed_data, 'anatomicalAreaCode') AS anatomicalAreaCode,
        JSONExtractString(raw_data, 'aiResult', 'modelVersion') AS modelVersion,
        JSONExtractString(raw_data, 'aiResult', 'seriesIUID') AS seriesIUID,
        raw_data = 'null' AS is_raw_null
    FROM dwh_views.v_eris_report
    WHERE app_source = 'CDS'
    AND parseDateTimeBestEffortOrNull(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) >= '2025-10-01 00:00:00'),
aggregated AS (
    SELECT studyIUID, taskId, kafkaAiQueueId, modelId,
        min(downloadStartDT) AS downloadStartDT, max(downloadEndDT) AS downloadEndDT,
        min(processStartDT) AS processStartDT, max(processEndDT) AS processEndDT,
        min(pumStudyReadyForAiTime) AS pumStudyReadyForAiTime,
        max(pumStudyAcceptedTime) AS pumStudyAcceptedTime,
        max(pumReportSentTime) AS pumReportSentTime,
        max(pumReportLatestDicomInstanceTime) AS pumReportLatestDicomInstanceTime,
        arrayFirst(x -> x != '', groupUniqArray(error)) AS error,
        arrayFirst(x -> x != '', groupUniqArray(description)) AS description,
        arrayFirst(x -> x != '', groupUniqArray(norma)) AS norma,
        arrayFirst(x -> x != false, groupUniqArray(pathologyFlag)) AS pathologyFlag,
        arrayFirst(x -> x != 0, groupUniqArray(confidenceLevel)) AS confidenceLevel,
        arrayFirst(x -> x != '', groupUniqArray(modality)) AS modality,
        arrayFirst(x -> x != '', groupUniqArray(anatomicalAreaCode)) AS anatomicalAreaCode,
        arrayFirst(x -> x != '', groupUniqArray(modelVersion)) AS modelVersion,
        arrayFilter(x -> x != '', groupUniqArray(seriesIUID)) AS seriesIUID,
        max(is_raw_null) AS is_raw_null
    FROM prepared_data GROUP BY studyIUID, taskId, kafkaAiQueueId, modelId),
final_calculations AS (
    SELECT *,
        if(error != '' OR is_raw_null, 1, 0) AS "Дефект Б",
        greatest(pumReportSentTime, pumReportLatestDicomInstanceTime) AS pumReportLatestTime,
        dateDiff('second', pumStudyReadyForAiTime, greatest(pumReportSentTime, pumReportLatestDicomInstanceTime)) / 60.0 AS time_diff_minutes,
        CASE modelId
            WHEN 1002 THEN 'Care Mentor AI' WHEN 1003 THEN 'Третье мнение РГ' WHEN 1004 THEN 'Цельс ММГ'
            WHEN 1006 THEN 'FBM РГ ОГК' WHEN 1015 THEN 'Цельс ФЛГ' WHEN 1026 THEN 'IRYM MAMMOLens'
            WHEN 1027 THEN 'AIRadiology' WHEN 1040 THEN 'TrioDM (MTL)' WHEN 1054 THEN 'Care Mentor AI, FLATFOOT'
            WHEN 1059 THEN 'IMV MS' WHEN 1062 THEN 'Цельс РГ' WHEN 1063 THEN 'FBM ФЛГ'
            WHEN 1067 THEN 'Третье Мнение ФЛГ' WHEN 1068 THEN 'CVisionRad - Knee Arthrosis'
            WHEN 1075 THEN 'CVL - Chest CT Aorta' WHEN 1089 THEN 'АИ Диагностик МРТ ПКОП'
            WHEN 1090 THEN 'CVL - Abdomen CT Aorta' WHEN 1093 THEN 'Multivox ASPECTS CT ГМ'
            WHEN 1094 THEN 'Oxytech Spine XR Scoliosis' WHEN 1095 THEN 'Синапс Нейро. РГ Сколиоз'
            WHEN 1096 THEN 'ТретьеМнение.Маммограммы' WHEN 1099 THEN '.Цельс ОМС (ММГ)'
            WHEN 1100 THEN 'Chest-IRA' WHEN 1101 THEN 'Цельс КС КТ ОГК' WHEN 1102 THEN 'CVL - Chest CT Complex'
            WHEN 1120 THEN 'АрхиМед Aivory Chest X' WHEN 1123 THEN 'AIRadiology РГ синусит'
            WHEN 1125 THEN 'АИ Диагностик МРТ ГМ' WHEN 1128 THEN 'IMV GLIOMAS' WHEN 1129 THEN 'Sciberia Head'
            WHEN 1132 THEN 'АИ Диагностик Остеопороз Abd' WHEN 1134 THEN 'СберМед КТ ГМ ишемия'
            WHEN 1135 THEN 'АИ Диагностик Почки Abd' WHEN 1137 THEN 'СберМед КТ ГМ кровоизлияния'
            WHEN 1138 THEN 'VisionLabs Kidney Tumor Detector' WHEN 1139 THEN 'CVL - Abdomen CT Spine fracture'
            WHEN 1144 THEN 'IMV МРТ ПКОП' WHEN 1145 THEN 'АИ Диагностик КТ ОГК комплекс'
            WHEN 1147 THEN 'Esper.Scoliosis' WHEN 1148 THEN 'АрхиМед Aivory AI Nose X'
            WHEN 1149 THEN 'Oxytech РГ синусит' WHEN 1150 THEN '.Третье Мнение ММГ (ОМС)'
            WHEN 1151 THEN 'Abdomen-IRA' WHEN 1152 THEN 'АИ Диагностик образования надпочечников Abd'
            WHEN 1153 THEN 'АИ Диагностик Аорта Abd' WHEN 1155 THEN 'Третье мнение КТ ОГК комплекс'
            WHEN 1159 THEN 'Oxytech РГ коленного сустава' WHEN 1160 THEN 'А-рг-сколиоз'
            WHEN 1164 THEN 'Цельс КС КТ ГМ' WHEN 1165 THEN 'IMV PIRADS' WHEN 1166 THEN 'Oxytech Spine XR Spondylolisthesis'
            WHEN 1167 THEN 'NTechMed CT Brain Complex' WHEN 1168 THEN 'NtechMed MRI Brain'
            WHEN 1169 THEN 'АИ Диагностик КТ ГМ' WHEN 1172 THEN 'IMV МРТ ШОП'
            WHEN 1173 THEN 'АИ Диагностик мочекаменная болезнь Abd' WHEN 1174 THEN 'АИ Диагностик КТ ГМ морфометрия'
            WHEN 1175 THEN 'NTechMed Norm Brain CT' WHEN 1176 THEN 'NTechMed Norm Brain MRI'
            WHEN 1177 THEN 'А-рг-синусит' WHEN 1178 THEN 'Oxytech РГ перелом тазобедренного сустава'
            WHEN 1180 THEN 'Третье мнение КТ ГМ комплекс' WHEN 1181 THEN '.FBM ФЛГ (ОМС)'
            WHEN 1182 THEN '.FBM РГ (ОМС)' WHEN 1183 THEN 'СППР МРТ ПКОП' WHEN 1185 THEN '.Третье мнение ФЛГ (ОМС)'
            WHEN 1186 THEN '.Цельс ФЛГ (ОМС)' WHEN 1187 THEN '.Цельс РГ (ОМС)' WHEN 1188 THEN 'Cognitic-MRI-T'
            WHEN 1190 THEN '.Третье мнение РГ (ОМС)' WHEN 1192 THEN 'Cholelithiasis-IRA'
            WHEN 1196 THEN 'АИ Диагностик КТ ГМ комплекс' WHEN 1197 THEN 'АИ Диагностик КТ ОБП комплекс'
            WHEN 1198 THEN 'FBM PesPlanus' WHEN 1200 THEN 'Oxytech РГ перелом плечевого сустава'
            WHEN 1201 THEN 'Oxytech РГ плоскостопие' WHEN 1202 THEN 'Oxytech РГ перелом голеностопного сустава'
            WHEN 1203 THEN 'Oxytech РГ перелом лучезапястного сустава' WHEN 1204 THEN 'Oxytech КТ ОБП остеопороз'
            WHEN 1205 THEN 'IMV SUBCORTICAL' WHEN 1206 THEN 'IMV МРТ ГОП' WHEN 1207 THEN 'Третье мнение КТ ГМ морфометрия'
            WHEN 1208 THEN 'ЛОР КТ РГ синусит' WHEN 1209 THEN 'Oxytech РГ переломы позвоночника'
            WHEN 1210 THEN 'Цельс МРТ ОМТ' WHEN 1212 THEN 'СберМедИИ MoonShot'
            WHEN 1213 THEN 'Oxytech РГ костный возраст' WHEN 1214 THEN 'Oxytech КТ ОБП надпочечники'
            WHEN 1215 THEN 'Oxytech КТ ОБП аорта' WHEN 1216 THEN 'Oxytech КТ ОБП мочекаменная болезнь'
            WHEN 1217 THEN 'Oxytech КТ ОБП почки' WHEN 1218 THEN 'Oxytech РГ ОГК' WHEN 1219 THEN 'Oxytech ФЛГ'
            WHEN 1220 THEN 'Oxytech КТ ОБП печень' WHEN 1221 THEN 'Oxytech КТ ОБП желчнокаменная болезнь'
            WHEN 1226 THEN 'АрхиМед Aivory Pirads AI MR' WHEN 1227 THEN 'HealthSpine XR (сколиоз)'
            WHEN 1228 THEN 'HealthSpine XR (компрессионные переломы)' WHEN 1229 THEN 'Эирвэй РГ ТБС артроз'
            WHEN 1230 THEN 'Эирвэй РГ переломы тел позвонков' WHEN 1231 THEN 'IMV UTERUS'
            WHEN 1232 THEN 'Oxytech КТ ОБП морфометрия почек' WHEN 1233 THEN 'Oxytech КТ ОБП морфометрия печени'
            WHEN 1234 THEN 'Oxytech КТ ОБП морфометрия поджелудочной железы и селезенки'
            WHEN 1235 THEN 'Multivox Cerebrum' WHEN 1236 THEN 'RAZUM MEDX' WHEN 1237 THEN 'NtechMed MRI Brain GLIOMAS'
            WHEN 1238 THEN 'Эирвэй РГ перелом плечевого сустава' WHEN 1239 THEN 'Эирвэй РГ перелом лучезапястного сустава'
            WHEN 1240 THEN 'Эирвэй РГ перелом голеностопного сустава' WHEN 1241 THEN 'Эирвэй РГ ТБС перелом'
            WHEN 1242 THEN 'Эирвэй КТ ОБП. Образование печени' WHEN 1243 THEN 'Эирвэй КТ ОБП. Образование надпочечников'
            WHEN 1244 THEN 'Эирвэй КТ ОБП. Образование почек' WHEN 1245 THEN 'Эирвэй КТ ОБП. Мочекаменная болезнь'
            WHEN 1246 THEN 'Эирвэй КТ ОБП. Остеопороз' WHEN 1247 THEN 'Эирвэй КТ ОБП. Желчнокаменная болезнь'
            WHEN 1248 THEN 'Эирвэй КТ ОБП. Аневризма аорты' WHEN 1249 THEN 'ЛОР КТ РГ перелом лучезапястного сустава'
            WHEN 1250 THEN 'ЛОР КТ РГ перелом плечевого сустава' WHEN 1253 THEN '.TrioDM (MTL) ОМС'
            ELSE 'Неизвестный сервис'
        END AS "Сервис"
    FROM aggregated),
best_vri AS (
    SELECT study_id, descr_result_doctor_fio, descr_result_description, descr_result_conclusion, descr_result_doctor_job_name,
        row_number() OVER (PARTITION BY study_id ORDER BY
            CASE WHEN descr_result_conclusion != '' AND descr_result_doctor_job_name != '' AND descr_result_doctor_job_name != 'ERISPUM' AND NOT startsWith(descr_result_doctor_fio, 'Не о') THEN 0 ELSE 1 END,
            CASE WHEN descr_result_doctor_job_name = 'Врач-рентгенолог' THEN 0 ELSE 1 END, rand()) AS rn
    FROM data_views.v_route_instrumental
    WHERE study_id IS NOT NULL AND descr_result_conclusion != '' AND descr_result_doctor_job_name != ''
      AND descr_result_doctor_job_name != 'ERISPUM' AND NOT startsWith(descr_result_doctor_fio, 'Не о'))
SELECT fc.studyIUID, fc.kafkaAiQueueId, fc.modelId,
    formatDateTime(fc.pumStudyReadyForAiTime, '%d.%m.%Y %H:%i:%S') AS pumStudyReadyForAiTime,
    fc.description, fc.norma, fc.pathologyFlag, fc.confidenceLevel, fc.modelVersion, fc.seriesIUID,
    fc."Дефект Б" AS defect_b,
    formatDateTime(fc.pumReportLatestTime, '%d.%m.%Y %H:%i:%S') AS pumReportLatestTime,
    fc.time_diff_minutes,
    if(fc.modelId IN (1150,1099,1062,1063,1067,1096,1181,1182,1185,1186,1187,1188,1190,1253),
        if(fc.time_diff_minutes >= 3, 1, 0), if(fc.time_diff_minutes >= 6.5, 1, 0)) AS defect_a,
    fc."Сервис" AS servis,
    vri.descr_result_doctor_fio, vri.descr_result_description, vri.descr_result_conclusion
FROM final_calculations fc
LEFT JOIN best_vri vri ON fc.studyIUID = vri.study_id::String AND vri.rn = 1
WHERE fc.norma IN (0) AND fc.modelId IN (1181, 1182)"""

        result = client.query(query)
        client.close()
        client = None
        columns = ['studyiuid', 'kafkaaiqueueid', 'modelid', 'pumstudyreadyforaitime',
                   'description', 'norma', 'pathologyflag', 'confidencelevel', 'modelversion', 'seriesiuid',
                   'defect_b', 'pumreportlatesttime', 'time_diff_minutes', 'defect_a', 'servis',
                   'descr_result_doctor_fio', 'descr_result_description', 'descr_result_conclusion']
        df = pd.DataFrame(result.result_rows, columns=columns)
        with open(_BUFFER_РАСХОЖДЕНИЕ, 'wb') as f:
            _pickle.dump(df, f)
        print(f"  ✅ [расхождение] extract_phase: сохранено {len(df)} строк в буфер.")
        return True
    except Exception as e:
        print(f"  ❌ [расхождение] extract_phase: {e}")
        if client:
            client.close()
        return False


def load_phase():
    """Фаза 2 (VPN выключен): pickle-буфер → target ClickHouse (TRUNCATE + INSERT)."""
    print("📤 [расхождение] load_phase: загрузка из буфера в target ClickHouse...")
    try:
        with open(_BUFFER_РАСХОЖДЕНИЕ, 'rb') as f:
            df = _pickle.load(f)
    except Exception as e:
        print(f"  ❌ [расхождение] load_phase: не удалось прочитать буфер: {e}")
        return False

    if df is None or df.empty:
        print("  ⚠️ [расхождение] load_phase: буфер пуст, пропускаю.")
        return True

    success = write_df_to_ch(df)
    if success:
        print(f"  ✅ [расхождение] load_phase: выгружено {len(df)} строк в {CH_TABLE}.")
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
