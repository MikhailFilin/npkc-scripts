-- INPUT: data_views.v_eris_assignment_results, data_views.v_instrumental_examinations, dwh_views.v_eris_report, dm_ap2.dct_lpu
-- OUTPUT: разовый запрос для DBeaver, период 2026-04-01..2026-06-30, mu_id=100001912827
SELECT
    toDate(toTimeZone(toDateTime(t2.conduct_date), 'Europe/Moscow')) AS conduct_date,
    t2.payment_source,
    t2.device_type,
    t2.assessment_result_type_code,
    t2.conduct_mo_name,
    t2.conduct_mu_name,
    t2.assignment_describe_mu_id,
    lpu.lpu_short_name AS lpu_short_name_by_lpu_id,
    ie.assignment_mu_id,
    lpu_assign.lpu_short_name AS lpu_short_name_by_assignment_mu_id,
    substring(t2.diag_code, 1, 1) AS diag_code_letter,
    ai_res.norma_value,
    multiIf(
        ie.patient_age >= 18, 'Взрослые',
        ie.patient_age IS NOT NULL AND ie.patient_age < 18, 'Дети',
        'Не указано'
    ) AS age_group,
    countDistinct(t2.accession_number) AS cnt,
    toTimeZone(now(), 'Europe/Moscow') AS load_datetime
FROM data_views.v_eris_assignment_results t2
LEFT JOIN dm_ap2.dct_lpu AS lpu
    ON lpu.lpu_id = t2.assignment_describe_mu_id
LEFT JOIN (
    SELECT accession_number, any(patient_age) AS patient_age, any(assignment_mu_id) AS assignment_mu_id
    FROM data_views.v_instrumental_examinations
    WHERE accession_number != ''
    GROUP BY accession_number
) ie ON t2.accession_number = ie.accession_number
LEFT JOIN dm_ap2.dct_lpu AS lpu_assign
    ON lpu_assign.lpu_id = ie.assignment_mu_id
LEFT JOIN (
    SELECT
        JSONExtractString(raw_data, 'studyIUID') AS studyIUID,
        min(JSONExtractString(JSONExtractString(raw_data, 'aiResult'), 'norma')) AS norma_value
    FROM dwh_views.v_eris_report
    WHERE app_source = 'CDS'
      AND parseDateTimeBestEffortOrNull(JSONExtractString(computed_data, 'pumStudyReadyForAiTime')) IS NOT NULL
      AND JSONExtractString(raw_data, 'studyIUID') != ''
    GROUP BY studyIUID
) ai_res ON t2.study_uid = ai_res.studyIUID
WHERE t2.accession_number != ''
  AND t2.assignment_describe_mu_id = '100001912827'
  AND toDate(t2.conduct_date) >= '2026-04-01'
  AND toDate(t2.conduct_date) < '2026-07-01'
GROUP BY
    conduct_date,
    t2.payment_source,
    t2.device_type,
    t2.assessment_result_type_code,
    t2.conduct_mo_name,
    t2.conduct_mu_name,
    t2.assignment_describe_mu_id,
    lpu.lpu_short_name,
    ie.assignment_mu_id,
    lpu_assign.lpu_short_name,
    diag_code_letter,
    ai_res.norma_value,
    age_group
ORDER BY conduct_date DESC, t2.payment_source ASC
