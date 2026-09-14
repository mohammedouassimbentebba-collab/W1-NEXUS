# Step 48 — Free Intelligence Discovery + Conversation Context Efficiency

تهدف هذه الخطوة إلى ربط **Dreamer Mode** بمصادر سعة فعلية مع الحفاظ على الفكرة الأساسية في W1 Nexus: تعاون عدة نماذج تحت Orchestrator واحد، وليس مجرد اختيار أرخص نموذج.

## 1. Free Intelligence Discovery

أضيفت طبقة `FreeIntelligenceDiscovery` بحيث تستطيع واجهة Connections البحث بأمان عن:

- Ollama محلي على loopback، ويمكن لـW1 تبنّي النماذج المكتشفة تلقائيًا كسعة `local / unlimited / zero-cost`.
- بوابات محلية اختيارية متوافقة مع OpenAI مثل FreeLLMAPI و9Router عندما يشغّلها المستخدم بنفسه.
- قوائم النماذج وراء Connections التي ربطها المستخدم مسبقًا.

الاكتشاف لا يساوي الاستحقاق: رؤية model id لا تعني أن الحساب يملك حصة مجانية. لا يستورد W1 cookies أو browser sessions، ولا يفترض أن اشتراك تطبيق استهلاكي يمنح API access.

البوابات الخارجية/التجميعية تبقى opt-in، و`terms_status=unknown` إلى أن تتم مراجعة شروط المزود الفعلية. لذلك لا تدخل تلقائيًا في routing عندما تكون السياسة `require_known_terms=true`.

## 2. ConversationStore

أضيف مخزن محادثات SQLite محلي مستقل:

- يحتفظ بكل الرسائل محليًا بدل خسارة التاريخ عند إغلاق الواجهة.
- يخزن الدور، النص، model id الاختياري، metadata، hash، وتسلسل الرسائل.
- يدعم summaries صريحة، دون اختراع تلخيص خفي غير قابل للمراجعة.

## 3. Token-bounded context packs

عند بناء سياق لنموذج، لا يعيد W1 إرسال كامل التاريخ. `ConversationStore.build_context` يبني pack محدودًا بميزانية توكن ويعطي الأولوية إلى:

1. summary صريحة إن وجدت.
2. ذكريات طويلة الأمد ذات صلة من Memory Fabric ضمن جزء مستقل من الميزانية.
3. أحدث الرسائل الدقيقة التي تتسع داخل الميزانية.

كل الرسائل التي لا تدخل الـpack تبقى محفوظة محليًا ويمكن الرجوع إليها لاحقًا.

## 4. Memory safety

رسائل النماذج تُحفظ كسجل محادثة، لكنها **لا تُرقّى تلقائيًا إلى Long-Term Memory**. ترقية معلومة إلى ذاكرة طويلة الأمد تظل عملية منفصلة ومحكومة، حتى لا تتحول تخمينات نموذج إلى حقائق دائمة.

## 5. Desktop UX

أضيف إلى Connections قسم **Free Intelligence Discovery** مع:

- Discover free & local capacity
- Scan connected providers
- Include local third-party gateways (off by default)
- نتائج توضح available / detected / auth required / not detected / opt-in required
- Adopt local Ollama
- Connect detected OpenAI-compatible gateway

وأضيف إلى Chat:

- سجل محادثات محلي دائم.
- إنشاء محادثة جديدة.
- Token budget control.
- Build efficient context.
- مؤشرات full history tokens / context pack tokens / estimated savings.

كما يستطيع Home/Dreamer Mode إجراء discovery تلقائي قبل التخطيط. إذا عثر على Ollama محلي صالح، يستطيع تبنيه وإعادة محاولة بناء الفريق التكيفي. لا يتم auto-adopt للبوابات الخارجية.

## 6. Windows portability

تم إصلاح false negative في Artifact Studio benchmark على Windows: مقارنة code preview أصبحت تطبع CRLF/LF إلى LF قبل التحقق، دون تغيير محتوى ملفات المستخدم.

## حدود Step 48

- لا يقوم W1 بكشط الإنترنت بحثًا عن API keys مجانية أو تجاوز حصص المزودين.
- لا يثبت FreeLLMAPI أو 9Router تلقائيًا.
- لا يستنتج free-tier/billing من مجرد ظهور نموذج.
- live quota telemetry الدقيقة لكل مزود ما تزال تحتاج adapters خاصة بالمزود.
- live collaborative inference عبر مزود حقيقي سيُختبر بعد ربط Connection فعلية.
