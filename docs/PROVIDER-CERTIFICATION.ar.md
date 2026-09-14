# W1 Nexus™ — Live Provider Certification

الإصدار الأول للعقد: `1.0` — Step 44 / dev44.

هذه الطبقة لا تضيف طريقة جديدة لتجاوز نظام دخول مزود الذكاء الاصطناعي، ولا تفترض أن اشتراك تطبيق المستهلك يمنح API access. وظيفتها هي **قياس الاتصال الذي أضافه المستخدم رسميًا إلى W1** وإنتاج دليل محكوم يبين ما الذي اختُبر حيًا فعلًا، وعلى أي مزود/حساب/نموذج وفي أي وقت.

## القاعدة الحاكمة

لا تكفي اختبارات W1 المحلية للقول إن Claude أو Gemini أو OpenAI أو Grok «Certified». يوجد فصل صريح بين حالتين:

- **Certification harness verified**: منطق التخطيط، fail-closed، redaction، evidence hashing، request routing، والـbenchmark المحلي نجح دون اتصال خارجي.
- **Provider live certified**: نفذت W1 probes فعلية باستخدام credential يملكه المستخدم، وسُجلت نتيجة حيّة لنموذج محدد. لا توجد هذه الحالة تلقائيًا بعد تثبيت W1.

شهادة مزود حيّة ليست ضمانًا دائمًا؛ APIs والنماذج والسياسات تتغير. لذلك كل record يحتفظ بوقت الاختبار، المزود، النموذج، مستوى الاختبار، host، snapshot للعقد، hashes للأدلة، وحالة كل probe.

## المستويات

### `preflight`

ينفذ configuration/credential checks محليًا ثم model discovery حيًا عندما يملك المزود endpoint معروفًا للقائمة. لا ينفذ generation مقصودًا، ولذلك لا يحجز W1 مكالمات model billable ضمن هذا المستوى.

### `runtime`

يشمل preflight ثم ينفذ طلب W1 حقيقي عبر الـProvider Connector الحالي، ويتحقق أن المزود/النموذج يستطيع إعادة W1 structured contribution contract. بالنسبة للمزودات السحابية قد تكون هذه مكالمة مدفوعة، ولذلك يرفض W1 التنفيذ ما لم يمرر المستخدم acknowledgment صريحًا.

### `full`

يشمل runtime ثم يضيف probes مستقلة لـstreaming وnative tool/function calling. هذا المستوى قد يستهلك حتى ثلاث model-generation calls سحابية، فوق model discovery غير التوليدي، ولذلك يبقى fail-closed حتى `--allow-billable-probes`.

## الأمان والخصوصية

- لا توجد network calls من `certification-plan` أو `certification-benchmark`.
- `certify` يرفض التنفيذ بدون `--live`.
- cloud runtime/full يرفض التنفيذ بدون `--allow-billable-probes`.
- الـcredential يبقى داخل Credential Broker / OS vault؛ certification database لا تخزن السر الخام.
- لا تخزن response bodies الخام أو نص المحادثة الناتج. يخزن W1 fingerprint SHA-256 وmetadata محدودة فقط.
- provider endpoint يظل خاضعًا لحماية AI Connections: المزودات الرسمية لا يسمح بتحويل مفاتيحها إلى host مختلف، بينما custom remote endpoints تحتاج HTTPS وHTTP المحلي يقتصر على loopback.
- لا تستخرج W1 cookies أو session tokens من ChatGPT/Claude/Grok أو تطبيقات أخرى.

## عقود المزودات في snapshot dev44

يحتفظ W1 بجدول machine-readable في `provider_certification.py`. snapshot تاريخ 2026-08-09 يربط runtime الحالي بالسطوح التالية: OpenAI Responses API، Anthropic Messages API، Gemini Interactions API للـAPI-key connections مع generateContent compatibility لمسار OAuth الحالي، وxAI Responses API. Local/Custom تبقى OpenAI-compatible وفق endpoint الذي يختاره المستخدم.

هذا الجدول **snapshot لا مصدر حقيقة أبدي**؛ يجب تحديثه عندما تتغير وثائق المزودات، ولا تتحول أي خانة منه إلى live certification من تلقاء نفسها.

## CLI

```text
w1 connections certification-catalog
w1 connections certification-benchmark
w1 connections certification-plan <connection-id> --model <model> --level preflight|runtime|full
w1 connections certify <connection-id> --model <model> --level runtime --live --allow-billable-probes
w1 connections certifications [--connection <connection-id>]
```

يمكن تنفيذ `preflight` بدون `--allow-billable-probes`، لكن `--live` يظل مطلوبًا لأنه قد يقوم بطلب حي للـmodel catalog. لا يفتح W1 الـnative credential vault قبل أن يمرّ المستخدم عبر حواجز التنفيذ الصريحة اللازمة.

## ما يثبته dev44 وما لا يثبته

يثبت dev44 أن **Certification Harness نفسها** تعمل deterministically وتغلق المسارات الحساسة وتنتج evidence منزوعة الأسرار. لا يدعي dev44 أن OpenAI أو Anthropic أو Gemini أو xAI قد اجتازوا live certification في بيئة البناء الحالية؛ عدد هذه الشهادات في scorecard هو صفر ما لم تسجل مكالمات حقيقية من حساب المستخدم.
