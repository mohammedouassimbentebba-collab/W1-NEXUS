# Step 49 — W1 Intelligence Search Engine (Local-First Federated Discovery)

## الهدف

بناء محرك بحث عن مصادر الذكاء الاصطناعي والنماذج والبوابات المجانية/المفتوحة **دون امتلاك W1 لأي خادم مركزي**.

بدل إنشاء فهرس سحابي خاص بنا، يعمل المحرك داخل جهاز المستخدم نفسه:

1. يرسل طلبات HTTPS عند الطلب إلى مصادر عامة محددة مسبقًا.
2. يجمع metadata عامة فقط.
3. يخزن النتائج في SQLite محلي داخل workspace.
4. لا يخزن credentials داخل Search Store.
5. لا يعتبر مجرد ظهور نموذج أو مشروع دليلاً على entitlement أو free quota.
6. لا يضيف نتيجة طرف ثالث تلقائيًا إلى Adaptive Capacity Router.

## المعمارية

```text
User query
   |
   v
Local W1 Nexus process (127.0.0.1)
   |
   +--> OpenRouter public model catalog
   +--> GitHub Repository Search API
   +--> Hugging Face Hub model API
   |
   v
Normalization + provenance + conservative classification
   |
   v
.w1nexus/intelligence-search.sqlite3
   |
   v
Manual review / Connect / Local deployment decision
   |
   v
Free Intelligence Discovery -> Capacity Router -> AI Teams
```

لا يوجد W1 cloud index ولا telemetry service مملوك لنا.

## المصادر الأولى

### OpenRouter

يقرأ W1 public model catalog ويعرض فقط النماذج التي تحمل free variant أو pricing صفريًا في catalog. النتيجة تبقى `connectable` وتحتاج حساب/مفتاح المستخدم لدى المزود؛ لا يفترض W1 entitlement من البحث.

### GitHub

يستخدم Repository Search لاكتشاف مشاريع gateways / routers / OpenAI-compatible tooling. كل نتيجة GitHub تبقى `manual_review` و`terms_status=unknown`. النجوم أو forks ليست إشارة ثقة ولا تؤدي إلى auto-adoption.

يمكن للمستخدم وضع `GITHUB_TOKEN` في environment لزيادة حدود API؛ W1 لا يكتبه إلى قاعدة بيانات البحث.

### Hugging Face Hub

يبحث عن model repositories في text-generation ويصنفها `local_candidate`. وجود weights في Hub لا يعني hosted inference مجانيًا؛ لذلك لا يسجلها W1 كسعة zero-cost قابلة للتشغيل إلا بعد أن تصبح متاحة عبر runtime محلي أو provider connection موثقة.

## الأمن والخصوصية

- HTTPS فقط للمصادر العامة.
- host allowlist ثابت داخل الكود.
- لا scraping عشوائي لأي URL من نتائج البحث.
- لا browser cookies أو consumer-app sessions.
- لا API keys داخل Intelligence Search Store.
- no auto-route لنتائج third-party.
- response size وtimeout محدودان.
- البحث on-demand فقط؛ لا crawler مركزي ولا background cloud job.

## UX

تحت Connections يظهر قسم:

**W1 Intelligence Search Engine — Search the AI ecosystem from this computer**

ويحتوي:

- query input
- OpenRouter free catalog
- GitHub projects
- Hugging Face models
- Search
- result cards: source / free status / review status / score / provenance link

## CLI

```bash
w1 intelligence catalog
w1 intelligence benchmark
w1 intelligence search "reasoning" --source openrouter --source github
w1 intelligence list
w1 intelligence clear
```

## العلاقة مع Dreamer Mode

Step 49 لا يغير قاعدة الثقة: Search Engine يولد **Candidates** فقط. بعد المراجعة/الربط/التشغيل المحلي، تدخل النماذج المؤهلة إلى Free Intelligence Discovery وCapacity Router. بذلك يبقى التعاون بين النماذج منفصلاً عن البحث، ولا يتحول البحث إلى تثبيت أو استهلاك تلقائي غير آمن.

## حدود هذه الخطوة

- ليست Web Search عامة لكل الإنترنت؛ هي Federated AI Ecosystem Search فوق مصادر عامة معروفة.
- لا crawler مركزي لأن W1 لا يملك خادمًا.
- لا يتم تنزيل weights من Hugging Face تلقائيًا.
- لا يتم إنشاء حسابات أو API keys تلقائيًا.
- لا يتم التحقق من الشروط القانونية لمشروع GitHub بمجرد العثور عليه.
- جودة/توفر free hosted models يمكن أن تتغير لدى المصدر الخارجي؛ W1 يعيد التحقق عند الاستخدام الفعلي لاحقًا.
