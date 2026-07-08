-- INPUT: data_views.v_eris_assignment_results, data_views.v_instrumental_examinations, dm_ap2.dct_lpu
-- OUTPUT: агрегат по mu_id=100001912827 за 2026-04-01..2026-06-30 + assignment_mu_id (из instrumental по accession_number, дедуп) + lpu_short_name по lpu_id и по main_lpu_id
WITH ie AS (
    SELECT accession_number, assignment_mu_id
    FROM data_views.v_instrumental_examinations
    LIMIT 1 BY accession_number
)
SELECT
    toDate(r.conduct_date) AS conduct_date,
    r.device_type,
    r.assignment_describe_mu_id,
    r.conduct_mo_name,
    r.conduct_mu_name,
    r.diag_code,
    r.payment_source,
    r.assessment_result_type_code,
    ie.assignment_mu_id,
    lpu_by_id.lpu_short_name AS lpu_short_name_by_lpu_id,
    lpu_by_main.lpu_short_name AS lpu_short_name_by_main_lpu_id,
    COUNT(r.accession_number) AS cnt
FROM data_views.v_eris_assignment_results AS r
LEFT JOIN ie
    ON ie.accession_number = r.accession_number
LEFT JOIN dm_ap2.dct_lpu AS lpu_by_id
    ON lpu_by_id.lpu_id = ie.assignment_mu_id
LEFT JOIN dm_ap2.dct_lpu AS lpu_by_main
    ON lpu_by_main.main_lpu_id = ie.assignment_mu_id
WHERE r.conduct_date >= '2026-04-01' AND r.conduct_date < '2026-07-01'
  AND r.assignment_describe_mu_id = '100001912827'
GROUP BY
    toDate(r.conduct_date),
    r.device_type,
    r.assignment_describe_mu_id,
    r.conduct_mo_name,
    r.conduct_mu_name,
    r.diag_code,
    r.payment_source,
    r.assessment_result_type_code,
    ie.assignment_mu_id,
    lpu_by_id.lpu_short_name,
    lpu_by_main.lpu_short_name
ORDER BY toDate(r.conduct_date) DESC
