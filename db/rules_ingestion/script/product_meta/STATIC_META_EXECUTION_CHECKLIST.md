# StaticMeta Execution Checklist

Batch: 2026-06-24

Script: `db/rules_ingestion/script/product_meta/ingest_static_meta.py`

Raw snapshot: `db/rules_ingestion/data/raw/20260622`

Derived output: `db/rules_ingestion/data/derived/20260624/product_meta_static`

## Variables

- [x] `StaticMeta.product`
- [x] `StaticMeta.exchange`
- [x] `StaticMeta.name`
- [x] `StaticMeta.position_date_type`
- [x] `StaticMeta.max_margin_side_algorithm`
- [x] `StaticMeta.combination_type`

## Exchange Coverage

- [x] CFFEX: 8 candidate products
- [x] CZCE: 27 candidate products
- [x] DCE: 26 candidate products
- [x] GFEX: 5 candidate products
- [x] INE: 5 candidate products
- [x] SHFE: 20 candidate products

## Generated Files

- [x] `candidate_static_meta.csv`
- [x] `static_meta_multi_value_fields.csv`
- [x] `rejected_product_info_codes.csv`
- [x] `audit_report.md`
- [x] `yaml/*.yaml`

## Validation

- [x] Generated 91 candidate YAML files.
- [x] Verified all 91 YAML files can be loaded by `ProductMetaManager.get_static_meta()` through isolated module loading.
- [x] Recorded 29 `max_margin_side_algorithm` multi-value findings for follow-up confirmation.
- [x] Rejected 14 unsupported or unverified product-info codes.
