# TransactionCostRule Execution Checklist

Batch: 2026-06-24

Script: `db/rules_ingestion/script/product_meta/ingest_transaction_cost.py`

Raw snapshot: `db/rules_ingestion/data/raw/20260622`

Derived output: `db/rules_ingestion/data/derived/20260624/product_meta_transaction_cost`

## Variables

- [x] `TransactionCostRule.effective_date`
- [x] `TransactionCostRule.commission_type`
- [x] `TransactionCostRule.open`
- [x] `TransactionCostRule.close_yesterday`
- [x] `TransactionCostRule.close_today`
- [x] `TransactionCostRule.scope`
- [x] `TransactionCostRule.condition`

## Coverage

- [x] Target products from StaticMeta candidates: 91
- [x] Products with generated candidate histories: 87
- [x] Products with no usable settle rows: 4 (`LR`, `L_F`, `PP_F`, `V_F`)
- [x] Products with same-day contract-level fee tuple conflicts: 13
- [x] Generated rule rows: 124
- [x] Generated candidate YAML files: 87

## Generated Files

- [x] `transaction_cost_product_summary.csv`
- [x] `transaction_cost_rules.csv`
- [x] `transaction_cost_conflicts.csv`
- [x] `transaction_cost_bigquant_current_validation.csv`
- [x] `audit_report.md`
- [x] `yaml/*.yaml`

## Validation

- [x] `python -m py_compile db/rules_ingestion/script/product_meta/ingest_transaction_cost.py`
- [x] Verified all 87 generated YAML files can be loaded by `ProductMetaManager.get_transaction_cost()` through isolated module loading.
