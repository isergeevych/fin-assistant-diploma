# Результаты экспериментов Qdrant (сгенерировано scripts/qdrant_experiments.py)

Дата эксперимента: 2026-10-12. Модель: `qwen3-embedding:0.6b`, точек в коллекции: см. отчёт.

## Cosine vs Dot

| Запрос | top-5 COSINE | top-5 DOT | Ранжирование совпало |
|---|---|---|---|
| можно ли вернуть деньги за лечение зубов | `taxes/social_deduction_medical.pdf`#0 (0.560)<br>`budgeting/debt_strategies.md`#1 (0.396)<br>`taxes/tax_refund_timeline.md`#1 (0.395)<br>`deposits_credits/mortgage_basics.md`#1 (0.388)<br>`fraud_security/cooling_period.md`#0 (0.369) | `taxes/social_deduction_medical.pdf`#0 (0.560)<br>`budgeting/debt_strategies.md`#1 (0.396)<br>`taxes/tax_refund_timeline.md`#1 (0.395)<br>`deposits_credits/mortgage_basics.md`#1 (0.388)<br>`fraud_security/cooling_period.md`#0 (0.369) | да |
| мне звонят из банка и просят код из смс | `fraud_security/fake_bank_security_call.html`#1 (0.821)<br>`fraud_security/sms_codes.md`#0 (0.779)<br>`fraud_security/fake_bank_security_call.html`#0 (0.743)<br>`fraud_security/sms_codes.md`#1 (0.718)<br>`fraud_security/elderly_relatives.md`#1 (0.592) | `fraud_security/fake_bank_security_call.html`#1 (0.821)<br>`fraud_security/sms_codes.md`#0 (0.779)<br>`fraud_security/fake_bank_security_call.html`#0 (0.743)<br>`fraud_security/sms_codes.md`#1 (0.718)<br>`fraud_security/elderly_relatives.md`#1 (0.592) | да |
| сколько стоит подписка Плюс в приложении | `app_faq/tariffs_v1.md`#0 (0.673)<br>`app_faq/tariffs_v2.pdf`#0 (0.644)<br>`budgeting/subscriptions_audit.md`#0 (0.493)<br>`budgeting/subscriptions_audit.md`#1 (0.472)<br>`budgeting/big_purchase.md`#1 (0.424) | `app_faq/tariffs_v1.md`#0 (0.673)<br>`app_faq/tariffs_v2.pdf`#0 (0.644)<br>`budgeting/subscriptions_audit.md`#0 (0.493)<br>`budgeting/subscriptions_audit.md`#1 (0.472)<br>`budgeting/big_purchase.md`#1 (0.424) | да |
| что будет с вкладом если у банка отзовут лицензию | `deposits_credits/deposit_insurance_asv.pdf`#0 (0.600)<br>`deposits_credits/credit_self_ban.md`#1 (0.510)<br>`fraud_security/investment_scams.md`#1 (0.484)<br>`deposits_credits/microloans_risk.md`#1 (0.453)<br>`fraud_security/cooling_period.md`#2 (0.452) | `deposits_credits/deposit_insurance_asv.pdf`#0 (0.600)<br>`deposits_credits/credit_self_ban.md`#1 (0.510)<br>`fraud_security/investment_scams.md`#1 (0.484)<br>`deposits_credits/microloans_risk.md`#1 (0.453)<br>`fraud_security/cooling_period.md`#2 (0.452) | да |
| как погасить кредит раньше срока | `deposits_credits/early_repayment.docx`#0 (0.709)<br>`deposits_credits/refinancing.md`#1 (0.590)<br>`budgeting/debt_strategies.md`#0 (0.561)<br>`deposits_credits/annuity_vs_differentiated.md`#1 (0.529)<br>`fraud_security/cooling_period.md`#1 (0.524) | `deposits_credits/early_repayment.docx`#0 (0.709)<br>`deposits_credits/refinancing.md`#1 (0.590)<br>`budgeting/debt_strategies.md`#0 (0.561)<br>`deposits_credits/annuity_vs_differentiated.md`#1 (0.529)<br>`fraud_security/cooling_period.md`#1 (0.524) | да |

Итог: ранжирование совпало на **всех** запросах; временные коллекции documents_cosine / documents_dot удалены.

## Фильтры

### 0. Без фильтра (для сравнения)

Запрос: «сколько стоит подписка Плюс»

```python
query_filter=None
```

| # | source | category | status | created_at | score |
|---|---|---|---|---|---|
| 1 | `app_faq/tariffs_v1.md` | app_faq | archived | 2025-10-31 | 0.642 |
| 2 | `app_faq/tariffs_v2.pdf` | app_faq | active | 2026-09-19 | 0.579 |
| 3 | `budgeting/subscriptions_audit.md` | budgeting | active | 2026-09-19 | 0.511 |

Поиск: 3.6 мс

### 1. Match по строке: только FAQ приложения

Запрос: «как выгрузить отчёт»

```python
Filter(must=[FieldCondition(key="category", match=MatchValue(value="app_faq"))])
```

| # | source | category | status | created_at | score |
|---|---|---|---|---|---|
| 1 | `app_faq/export_reports.md` | app_faq | active | 2026-09-19 | 0.701 |
| 2 | `app_faq/export_reports.md` | app_faq | active | 2026-09-19 | 0.633 |
| 3 | `app_faq/telegram_bot.md` | app_faq | active | 2026-09-19 | 0.527 |

Поиск: 3.7 мс

### 2. Range по дате: только документы за последние 30 дней

Запрос: «сколько стоит подписка Плюс»

```python
Filter(must=[FieldCondition(key="created_at",
       range=DatetimeRange(gte="2026-09-12"))])  # today − 30 days
```

| # | source | category | status | created_at | score |
|---|---|---|---|---|---|
| 1 | `app_faq/tariffs_v2.pdf` | app_faq | active | 2026-09-19 | 0.579 |
| 2 | `budgeting/subscriptions_audit.md` | budgeting | active | 2026-09-19 | 0.511 |
| 3 | `budgeting/subscriptions_audit.md` | budgeting | active | 2026-09-19 | 0.501 |

Поиск: 3.0 мс

### 3. must + must_not: только FAQ, исключая архивные версии

Запрос: «сколько стоит подписка Плюс»

```python
Filter(
    must=[FieldCondition(key="category", match=MatchValue(value="app_faq"))],
    must_not=[FieldCondition(key="status", match=MatchValue(value="archived"))],
)
```

| # | source | category | status | created_at | score |
|---|---|---|---|---|---|
| 1 | `app_faq/tariffs_v2.pdf` | app_faq | active | 2026-09-19 | 0.579 |
| 2 | `app_faq/export_reports.md` | app_faq | active | 2026-09-19 | 0.312 |
| 3 | `app_faq/budgets_alerts.md` | app_faq | active | 2026-09-19 | 0.309 |

Поиск: 3.0 мс
