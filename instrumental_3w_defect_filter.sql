SELECT
    t.*,
    d.reason AS defect_reason,
    toUInt32(rand()) % 100 AS _bucket,
    dense_rank() OVER (ORDER BY toStartOfMinute(t.load_datetime) DESC) AS version_rank
FROM (
    SELECT
        i.*,
        coalesce(round(b.trauma_pct, 1), 0) AS trauma_probability_pct
    FROM dwh_test_db.instrumental_examinations_3w AS i
    LEFT JOIN dwh_test_db.eris_trauma_probability_buffer AS b
        ON i.conduct_mu_name = b.conduct_mu_name
       AND i.device_type = b.device_type
       AND i.research_name = b.research_name
) AS t
LEFT JOIN (
    SELECT accession_number, reason
    FROM dwh_test_db.defect
    WHERE reason NOT IN ('травма', '') AND reason IS NOT NULL
    ORDER BY entered_at DESC
    LIMIT 1 BY accession_number
) AS d ON t.accession_number = d.accession_number
WHERE t.accession_number NOT IN (
    SELECT accession_number
    FROM dwh_test_db.defect
    WHERE reason = 'травма' OR reason = '' OR reason IS NULL
)




SELECT
    t.*,
    toUInt32(rand()) % 100 AS _bucket,
    dense_rank() OVER (ORDER BY toStartOfMinute(t.load_datetime) DESC) AS version_rank
FROM (
    SELECT
        i.*,
        coalesce(round(b.trauma_pct, 1), 0) AS trauma_probability_pct
    FROM dwh_test_db.instrumental_examinations_3w AS i
    LEFT JOIN dwh_test_db.eris_trauma_probability_buffer AS b
        ON i.conduct_mu_name = b.conduct_mu_name
       AND i.device_type = b.device_type
       AND i.research_name = b.research_name
) AS t
ANTI JOIN dwh_test_db.defect AS d
    ON t.accession_number = d.accession_number


--старый
SELECT
    i.*,
    d.reason AS defect_reason,
    toUInt32(rand()) % 100 AS _bucket,
    dense_rank() OVER (ORDER BY toStartOfMinute(i.load_datetime) DESC) AS version_rank
FROM dwh_test_db.instrumental_examinations_3w AS i
LEFT JOIN (
    SELECT accession_number, reason
    FROM dwh_test_db.defect
    WHERE reason NOT IN ('травма', '') AND reason IS NOT NULL
    ORDER BY entered_at DESC
    LIMIT 1 BY accession_number
) AS d ON i.accession_number = d.accession_number
WHERE i.accession_number NOT IN (
    SELECT accession_number
    FROM dwh_test_db.defect
    WHERE reason = 'травма' OR reason = '' OR reason IS NULL
)
