# Step 50 — Verified Intelligence Activation Bridge

## الهدف

إغلاق الفجوة بين **العثور على مصدر ذكاء** وبين **السماح له بالدخول إلى Model Access / Adaptive Capacity Router** دون إضافة أي خادم تابع لـW1 ودون افتراض أن كلمة `free` تعني أن المستخدم يملك entitlement فعليًا.

Step 49 ينتج `SearchCandidate` فقط. Step 50 يضيف بوابة محلية صريحة:

```text
Intelligence Search Candidate
          |
          v
   Local Assessment Gate
          |
   +------+-------------------------+
   |                                |
OpenRouter                      GitHub / HF
   |                                |
existing connection?                |
   |                                +--> review/local-deployment only
persisted model discovery match?
   |
   v
Activation-ready
   |
explicit user activation
   |
ModelProfile + CapacityObservation
   |
third_party_free / quota unknown
   |
Adaptive Router (explicit opt-in still required)
```

## التغيير البنيوي المهم

أصبح **OpenRouter** Provider رسميًا داخل `AI Connections` في W1:

- `provider_id = openrouter`
- OpenAI-compatible connector
- API-key authentication
- `https://openrouter.ai/api/v1/chat/completions`
- model discovery عبر `https://openrouter.ai/api/v1/models`

هذا يصلح فجوة Step 49: كان محرك البحث يستطيع إظهار مرشح OpenRouter كـ`connectable`، لكن Provider Catalog لم يكن يحتوي اتصال OpenRouter أصليًا.

## قواعد التفعيل

### OpenRouter

المرشح لا يصبح `activation_ready` إلا إذا تحققت جميع الشروط:

1. `source_id=openrouter`.
2. `free_status=verified_free`.
3. `review_status=connectable`.
4. الشروط ليست `unknown`.
5. يوجد اتصال OpenRouter مفعّل بحالة `connected`.
6. الـ`model_id` نفسه موجود حرفيًا داخل `discovered_models` لذلك الاتصال.

بوابة التفعيل **لا تنفذ network call خفيًا**. هي تعتمد على دليل model discovery سبق للمستخدم تشغيله وحُفظ في اتصال المزود.

### GitHub

Repository ليس Model Capacity. حتى لو حمل كلمات مثل `free API` أو كان مفتوح المصدر، يبقى `manual_review_required` ولا يمكن تحويله مباشرة إلى ModelProfile.

### Hugging Face

وجود weights لا يثبت أن النموذج مثبت محليًا ولا أن hosted inference مجاني للمستخدم. لذلك النتيجة تبقى `local_runtime_required` ويجب أن تظهر فعليًا لاحقًا في runtime محلي قبل اعتمادها كسعة تشغيلية.

## ما يحدث بعد التفعيل

عند تفعيل مرشح OpenRouter مؤهل:

- ينشئ W1 `ModelProfile` مرتبطًا باتصال المستخدم الموجود.
- يسجل `quality_unbenchmarked=true` بدل اختراع تقييم جودة.
- ينشئ `CapacityObservation` بالقيم المحافظة التالية:
  - `source=third_party_free`
  - `quota_state=unknown`
  - `input_cost_per_million=0`
  - `output_cost_per_million=0`
  - `entitlement_verified=false`
  - `terms_status=verified_third_party`

وبالتالي لا يؤدي التفعيل إلى auto-routing. السياسة الافتراضية للـAdaptive Capacity Router ما زالت `allow_third_party_free=false`.

## سجل التحقق المحلي

يستخدم Step 50 نفس قاعدة SQLite المحلية الخاصة بمحرك البحث ويضيف:

- `intelligence_activation_state`
- `intelligence_activation_audit`

الأحداث `candidate.reviewed` و`candidate.activated` تدخل في سلسلة SHA-256 محلية append-only. لا تُخزن raw API keys أو tokens داخل هذا السجل.

## CLI

```bash
w1 intelligence activation-benchmark
w1 intelligence assess <candidate-id>
w1 intelligence review <candidate-id> --decision approved
w1 intelligence activate <candidate-id>
w1 intelligence activations
```

إذا كانت نتيجة `assess` هي `model_discovery_required`، يجب تشغيل discovery على اتصال OpenRouter أولًا، ثم إعادة التقييم.

## Desktop UX

بطاقات نتائج Intelligence Search تعرض الآن حالة Activation Gate:

- `not assessed`
- `connection_required`
- `model_discovery_required`
- `manual_review_required`
- `local_runtime_required`
- `activation_ready`
- `activated`

زر **Activate verified model** لا يظهر إلا بعد نجاح assessment.

## الخصوصية والبنية

لا يوجد:

- W1 cloud verification server
- central activation database
- credential sync
- background crawler
- auto-account creation
- auto-routing after activation

كل state جديد يبقى داخل workspace المحلي للمستخدم.
