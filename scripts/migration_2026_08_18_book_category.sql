-- migration_2026_08_18_book_category.sql
-- 目的: quota_publication_set 加 book_category 生成列,统一"清单 vs 定额 + 建筑工程 vs 专业工程"三桶区分。
-- 单源: 自动从 material_type + quota_system_type 派生,无冗余风险。
--
-- 用法 (psql 或 file_asset 服务启动时):
--   psql "$FILE_ASSET_DATABASE_URL" -f scripts/migration_2026_08_18_book_category.sql
--
-- 映射:
--   material_type = 'boq_standard'                              → 'boq_standard'        (清单规范)
--   quota_system_type = 'construction_regional'                 → 'construction_quota'  (建筑工程定额)
--   quota_system_type = 'industry_specialty'                    → 'industry_quota'      (专业工程定额)
--   其它 material_type (quota_supplement / quota_explanation /
--                       amendment_errata / related_notice)        → NULL (暂不入3 桶)

BEGIN;

ALTER TABLE quota_publication_set
  ADD COLUMN book_category VARCHAR(32) GENERATED ALWAYS AS (
    CASE
      WHEN material_type = 'boq_standard' THEN 'boq_standard'
      WHEN quota_system_type = 'construction_regional' THEN 'construction_quota'
      WHEN quota_system_type = 'industry_specialty' THEN 'industry_quota'
      ELSE NULL
    END
  ) STORED;

-- 部分索引: 只对可归类的行加速 (NULL 占比较小)
CREATE INDEX ix_quota_pubset_book_category
  ON quota_publication_set(book_category)
  WHERE book_category IS NOT NULL;

COMMIT;

-- 验证 (脚本同步执行):
--   SELECT book_category, count(*)
--   FROM quota_publication_set
--   GROUP BY book_category;
--
-- 预期 (现状18 条全是建筑工程定额):
--   construction_quota | 18