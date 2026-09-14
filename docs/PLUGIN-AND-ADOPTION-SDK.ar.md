# Plugin & Adoption SDK — Step 39

## الهدف

تثبت Step 39 سطحًا عامًا صغيرًا يمكن لمطوري الطرف الثالث البناء فوقه دون الاعتماد على الوحدات الداخلية المتغيرة. العقد العام الأول هو:

- `w1cip.sdk` بإصدار `1.0.0`.
- Plugin API بإصدار `1.0`.
- `w1-plugin.json` كـmanifest fail-closed.
- سجل Plugins خاص بكل Workspace مع نسخ محلية immutable-by-policy وبصمة SHA-256 للشجرة.
- grants صريحة قبل تمكين الإضافة.
- subprocess host بعقد JSON محدود وtimeout وعزل crash عن عملية W1 الأساسية.
- Conformance suite قابلة للتشغيل قبل التثبيت أو بعده.
- تكامل `provider.adapter` مع Multi-Model Access عبر `w1-plugin:<plugin-id>`.

## العقد العام المستقر

المطور الخارجي يجب أن يستورد من `w1cip.sdk` فقط إذا أراد الاعتماد على Compatibility Contract في Step 39. الوحدات الداخلية مثل `w1cip.model_access` أو `w1cip.plugin_system` تبقى واجهات تنفيذ داخلية خلال سلسلة 0.1 dev ما لم توثق بصورة منفصلة.

الرموز العامة الحالية:

- `SDK_VERSION`
- `PLUGIN_API_VERSION`
- `PluginContext`
- `PluginBase`
- `W1LocalClient`
- `LocalControlSettings`
- `ProviderRequest`
- `ProviderResponse`
- `SDK_COMPATIBILITY_CONTRACT`

سياسة التوافق في Plugin API 1.x: نفس major مطلوب، وRuntime ذو minor أحدث يقبل plugin minor أقدم أو مساويًا. التغييرات الكاسرة تتطلب major جديدًا.

## manifest

الملف `w1-plugin.json` يصرح بالهوية والإصدار والـentrypoint والقدرات والصلاحيات. الحقول غير المعروفة ترفض بدل تجاهلها حتى لا تتحول أخطاء spelling إلى صلاحيات أو سلوك غير مقصود.

القدرات المنفذة في Step 39:

- `provider.adapter`
- `lifecycle.health`

الصلاحيات المعلنة تشمل workspace/network/credential/memory/computer/tool host capabilities، لكنها لا تمنح وصولًا تلقائيًا. يجب أن تكون grants صريحة ومكتملة قبل enable.

## دورة التثبيت

1. `w1 plugins scaffold` أو إنشاء plugin يدويًا.
2. `w1 plugins validate` للتحقق من manifest وcompatibility وtree digest.
3. `w1 plugins conformance` لاختبار lifecycle والعقد.
4. `w1 plugins install` لنسخ المصدر إلى `.w1nexus/plugins/installed/<id>/<version>`.
5. grants صريحة ثم enable.
6. قبل كل تشغيل يتم التحقق من digest للنسخة المثبتة.

تغيير المصدر الأصلي بعد التثبيت لا يغير النسخة الفعالة. تغيير النسخة المثبتة نفسها يجعل التشغيل يفشل حتى إعادة تثبيت نسخة معروفة.

## subprocess host

Managed plugins لا تستورد داخل عملية W1 الرئيسية. يشغّل W1 one-shot Python subprocess، يمرر JSON على stdin ويستقبل JSON واحدًا على stdout. يقوم host بالتقاط stdout/stderr الخاصين بالplugin، ويحدد timeout وحجم output، ويزيل متغيرات البيئة التي تبدو secrets.

هذا يعزل crashes/exceptions والـprotocol pollution عن عملية W1، لكنه **ليس sandbox أمنيًا كاملاً**. Python plugin غير موثوق ما زال كودًا محليًا يستطيع نظريًا استخدام OS APIs. Permissions في Step 39 تحكم ما تكشفه W1 نفسها، وليست بديلًا عن OCI/VM isolation. لا ينبغي تثبيت كود طرف ثالث غير موثوق على أساس أن subprocess وحده sandbox.

## Provider Adapter

Model Profile من نوع `custom_plugin` يستطيع تحديد:

`plugin_factory = "w1-plugin:org.example.plugin"`

عندها يستخدم Model Access الـPluginManager الخاص بالWorkspace، ويتحقق من enabled/grants/integrity/compatibility قبل إنشاء adapter. الشكل القديم `module:callable` يبقى مدعومًا للتوافق الخلفي لكنه لا يحصل على ضمانات managed-plugin الجديدة.

## CLI

- `w1 plugins benchmark`
- `w1 plugins scaffold PATH --id ID --name NAME`
- `w1 plugins validate PATH`
- `w1 plugins conformance PATH_OR_ID`
- `w1 plugins install PATH [--grant-permission ...] [--enable]`
- `w1 plugins list|show|verify|enable|disable|grant|revoke|health`

## الحدود

Step 39 لا تحتوي بعد على marketplace موقّع، تنزيل remote packages، dependency resolver، أو tool/UI extension surfaces كاملة. كما لا تدعي OS sandboxing لكود Python الطرف الثالث. هذه الحدود موثقة حتى لا يتحول Plugin API إلى مسار تنفيذ غير محكوم.

## إضافة Step 40 غير الكاسرة

أضافت Step 40 إلى السطح العام `CollaborationClient` و`CollaborationServerSettings` و`SyncMutation`. هذه إضافة additive داخل عقد SDK 1.x ولا تغير Plugin API 1.0 أو سلوك exports السابقة.
