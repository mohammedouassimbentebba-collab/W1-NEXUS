# مذكرة Step 44 — Live Provider Certification Harness

التاريخ: 2026-08-09

## القرار

إضافة طبقة Provider Certification مستقلة فوق AI Connections وCredential Broker وProvider Connectors بدل اعتبار نجاح unit tests دليلًا على أن تكامل مزود سحابي يعمل حيًا.

## المنفذ

- catalog لعقود OpenAI/Anthropic/Gemini/xAI/local/custom.
- مستويات `preflight` و`runtime` و`full`.
- `--live` إلزامي لكل اتصال خارجي.
- acknowledgment مستقل للمكالمات السحابية التي قد تكون billable.
- immutable redacted evidence store مع SHA-256 integrity.
- model discovery + W1 runtime structured-contract probe + streaming + native tool/function calling بحسب المستوى.
- benchmark محلي deterministic لا يجري أي provider call.
- CLI وSDK exports وتكامل مع `w1 evaluate`.
- تحديث Gemini API-key runtime إلى Interactions API، مع إبقاء OAuth compatibility عبر generateContent.
- تحديث xAI runtime إلى Responses API مع إبقاء موصلات Chat-Completions السابقة للتوافق.

## حدود الادعاء

لا توجد credentials حقيقية للمزودات داخل بيئة إغلاق dev44. لذلك `provider_certification_harness_passed = true` لا يعني `provider_live_certified = true`. لا يسجل W1 شهادة حيّة إلا من record ناتج عن `certify --live`، والمزود/النموذج/الحساب/التاريخ جزء من نطاق الدليل.

## التقييم

لا يرفع Step 44 نسبة الـscorecard الوظيفية لمجرد وجود harness. الهدف هو تحويل عبارة «الاتصال مدعوم» إلى ادعاء يمكن اختباره وتخزين دليل له، لا تضخيم نسبة الاكتمال قبل إجراء الاختبارات الحية.
