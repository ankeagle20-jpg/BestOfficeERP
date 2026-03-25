-- customers.tax_number boş veya anlamsız ('None' vb.) ise,
-- önce musteri_kyc.yetkili_tcno (son KYC kaydı), yoksa notes içindeki
-- "Yetkili TC: ..." metninden T.C. ile doldurur.
--
-- Supabase SQL Editor veya psql ile çalıştırın. İsterseniz önce:
-- BEGIN; ... SELECT ... ROLLBACK; ile deneyin.

WITH latest_kyc AS (
    SELECT DISTINCT ON (musteri_id)
        musteri_id,
        yetkili_tcno
    FROM musteri_kyc
    ORDER BY musteri_id, id DESC
),
src AS (
    SELECT
        c.id,
        COALESCE(
            NULLIF(TRIM(k.yetkili_tcno), ''),
            NULLIF(
                TRIM(
                    SUBSTRING(
                        LOWER(COALESCE(c.notes, ''))
                        FROM 'yetkili tc:[[:space:]]*([0-9]+)'
                    )
                ),
                ''
            )
        ) AS tc_src
    FROM customers c
    LEFT JOIN latest_kyc k ON k.musteri_id = c.id
    WHERE
        (
            c.tax_number IS NULL
            OR TRIM(c.tax_number) = ''
            OR LOWER(TRIM(c.tax_number)) IN ('none', 'null', 'nan')
        )
        AND (
            NULLIF(TRIM(k.yetkili_tcno), '') IS NOT NULL
            OR c.notes ~* 'Yetkili[[:space:]]+TC[[:space:]]*:[[:space:]]*[0-9]+'
        )
)
UPDATE customers c
SET tax_number = s.tc_src
FROM src s
WHERE c.id = s.id
  AND s.tc_src IS NOT NULL
  AND TRIM(s.tc_src) <> '';
