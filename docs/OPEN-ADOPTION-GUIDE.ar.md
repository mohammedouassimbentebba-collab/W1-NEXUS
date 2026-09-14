# دليل تبني W1 Nexus في المنتجات الأخرى

## طرق التبني

يمكن للشركة أو المطور استعمال W1 Nexus عبر:

1. السطح العام `w1cip.sdk` داخل تطبيق Python.
2. Local Control API على جهاز المستخدم.
3. خادم MCP الخاص بـW1.
4. W1-CIP فقط مع Runtime من تنفيذ الشركة.
5. Managed Plugin بعقد Plugin API 1.0.
6. Endpoint خاص متوافق مع OpenAI أو External Application bridge.

## Compatibility Contract من Step 39

الاعتماد العام المستقر يبدأ من `w1cip.sdk`، وليس من كل modules الداخلية. يشمل السطح الأول Plugin primitives إضافة إلى `W1LocalClient` و`LocalControlSettings` و`ProviderRequest` و`ProviderResponse` للتضمين والاتصال المحلي. في Plugin API 1.x يجب أن يبقى major متوافقًا، ويستطيع Runtime أحدث في minor قبول plugin minor أقدم/مساوٍ. التغيير الكاسر يتطلب major جديدًا.

يمكن اختبار plugin قبل اعتماده عبر `w1 plugins conformance`. ويمكن دمج provider plugin مع Model Access باستخدام `w1-plugin:<plugin-id>` بدل import مباشر داخل عملية W1.

## عدم الارتباط بخوادم W1

يجب أن يبقى التطبيق قادرًا على:

- تخزين حالته محليًا أو داخل بنية الشركة.
- الاتصال بنماذج الشركة مباشرة.
- تعطيل أي Telemetry.
- العمل دون حساب W1.
- استبدال واجهة W1 بواجهة المنتج المضيف.

## الحوكمة والأمان

لا تعني إضافة Connector أو Plugin أن النموذج يحصل على سلطة تنفيذ أو وصول للسياق. تبقى الصلاحيات تحت W1-CIP وAction Runtime وContextGrant وPlugin grants.

Managed plugins تعمل في subprocess منفصل مع timeout وintegrity verification وبيئة منزوعة من متغيرات الأسرار المعروفة. هذا يعزل فشل plugin عن النواة لكنه لا يحول Python code غير الموثوق إلى sandbox OS. للعزل الأمني القوي يلزم Secure Execution/OCI أو حدود نظام تشغيل مناسبة.

## الترخيص

لم يُضف إلى الحزمة الحالية ترخيص برمجي نهائي باسم المستخدم. قبل النشر العام يجب اختيار ترخيص صريح، مثل ترخيص permissive أو نموذج ترخيص مزدوج، بعد مراجعة أهداف المجتمع والتبني التجاري.

غياب ملف ترخيص يعني قانونيًا أن إعادة الاستخدام العام ليست ممنوحة تلقائيًا، لذلك يجب إغلاق هذا القرار قبل فتح المستودع للجمهور أو طلب تبني الشركات.

## التبني الجماعي Self-hosted من Step 40

يمكن للمنتج المضيف تشغيل `CollaborationStore` و`CollaborationService` داخل بنيته أو تشغيل HTTP surface عبر `create_collaboration_server`. ويستطيع العميل استعمال `CollaborationClient` و`SyncMutation` من `w1cip.sdk`.

الهوية الجماعية هنا مستقلة عن حساب W1-hosted: Team/Principal وaccess token يملكه deployment نفسه. يخزن الخادم digest للtoken فقط. إذا كان endpoint خارج loopback فيجب تشغيله عبر TLS؛ العميل العام يرفض `http://` البعيد.

هذا يجعل W1 قابلًا للتضمين في شركة أو جامعة مع مزامنة ومشاركة مشروع من دون الحاجة إلى حساب أو خادم مملوك لـW1. Hosted cloud لاحقًا يجب أن يطبق نفس العقود بدل جعلها تبعية جديدة للنواة.
