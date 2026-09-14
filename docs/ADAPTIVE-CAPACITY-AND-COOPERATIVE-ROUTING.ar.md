# W1 Nexus — Adaptive Capacity Router + Cooperative Multi-Model Orchestration

## الهدف

هذه الطبقة لا تستبدل تعاون النماذج. وظيفتها اختيار أفضل نموذج متاح **لكل دور داخل الفريق** مع الحفاظ على topology التعاون نفسها.

مثال `challenge`:

```text
Producer(s) -> Challenger -> Synthesizer -> Final Result
```

إذا نفدت حصة نموذج Challenger، يختار W1 Challenger بديلًا ولا يحذف مرحلة الاعتراض. وإذا فشل Synthesizer الأساسي، يمكن للـruntime تجربة Synthesizer احتياطي بالترتيب.

## مصادر السعة

- `official_free`: طبقة API مجانية رسمية.
- `subscription_entitlement`: entitlement API أثبته المزود صراحة؛ اشتراك تطبيق المستهلك وحده لا يكفي.
- `promotional`: رصيد ترويجي محدود.
- `third_party_free`: وسيط مجاني خارجي؛ لا يفعّل تلقائيًا افتراضيًا.
- `paid`: API مدفوع.
- `local`: نموذج محلي.

W1 لا يستنتج أن ChatGPT Plus أو Google AI Pro أو أي اشتراك تطبيق آخر يمنح API access. يجب إثبات entitlement من واجهة المزود/العقد الرسمي.

## سياسة التوجيه

الأوضاع الحالية:

- `maximum_quality`
- `balanced`
- `maximum_free`
- `local_only`
- `custom_budget`

كل مرشح يمر أولًا على شروط الأهلية: حالة الشروط/provenance، سماح المصدر، حالة quota، الحد الأدنى للجودة، والميزانية. ثم يُرتب بحسب quality، المصدر، توفر quota، تكلفة المهمة المقدرة، وتنويع المزود في أدوار المراجعة/التحقق/التحدي.

## Budget + quota semantics

- `remaining_tokens` هو observation وليس وعدًا من W1.
- `exhausted` يمنع اختيار النموذج.
- `low` يخفض أولوية النموذج.
- الحد `max_task_cost_usd` يطبق على التكلفة التراكمية للأدوار الأساسية، وليس على كل نموذج بمعزل.
- نماذج fallback لا تدخل في التكلفة المتوقعة إلا إذا استُخدمت لاحقًا.

## Adaptive telemetry refresh

لتجنب هدر طلبات quota/status، لكل observation فاصل تحديث مقترح:

- low: 60s
- unknown/exhausted: 300s
- available: 900s
- local/unlimited: 1800s

هذه مجرد سياسة محلية؛ provider-specific telemetry adapters يمكن إضافتها لاحقًا عبر `CapacityTelemetryAdapter` بدون تغيير الـrouter.

## الأمان والشفافية

- قاعدة `capacity-router.sqlite3` لا تخزن API keys أو OAuth tokens.
- third-party free غير مسموح تلقائيًا.
- `terms_status=unknown` مرفوض افتراضيًا.
- `subscription_entitlement` يحتاج `entitlement_verified=true`.
- الـportfolio يسجل `consumer_subscription_api_access_assumed=false`.
- لا يُخفي W1 مزود التسليم أو provenance النموذج.

## CLI

```bash
w1 capacity benchmark
w1 capacity list
w1 capacity observe MODEL_ID --source official_free --quota-state available --remaining-tokens 100000
w1 capacity plan --task task.json --team-mode challenge --routing-mode maximum_free --quality-floor 0.75 --save-portfolio
```

## Desktop

يظهر Adaptive Capacity Router داخل AI Team Studio. عند تسجيل model يمكن تحديد source، quality estimate، terms/provenance، وتكلفة input/output التقريبية. زر `Adaptive / Dreamer Team` يبني portfolio ديناميكيًا ويحفظه إذا نجح التخطيط.

## حدود dev46

- لا توجد بعد adapters حية عامة تقرأ quota من كل مزود؛ observations تُدخل محليًا أو تأتي من metadata إلى أن تتم إضافة telemetry رسمية لكل provider.
- تقدير الجودة يعتمد على capability scores المسجلة ولا يدعي benchmark خارجيًا تلقائيًا.
- لا يوجد context compression دلالي حي بين كل دورين بعد؛ الـruntime يحافظ على handoff الحقيقي الحالي.
- third-party aggregators تحتاج مراجعة شروط كل مزود قبل تمكين auto-routing.
