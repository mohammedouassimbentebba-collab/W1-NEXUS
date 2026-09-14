# Optional Collaboration Fabric — Step 40

تضيف هذه الخطوة طبقة تعاون جماعي **Self-hosted first** إلى W1 Nexus من دون تحويل السحابة إلى شرط تشغيل. يستطيع الفرد الاستمرار في تشغيل W1 محليًا بالكامل، بينما تستطيع شركة أو جامعة تشغيل Collaboration Fabric على خادم تملكه هي.

## المبادئ

1. **لا خادم W1 مملوك لنا مطلوب**: البروتوكول والـstore والـHTTP service تعمل محليًا أو على بنية المستخدم.
2. **Tenant boundaries صريحة**: كل Team له عضويات ومشاريع وصلاحيات مستقلة.
3. **لا Last-Write-Wins صامت**: المزامنة optimistic؛ stale writes تتحول إلى ConflictRecord ثم تحتاج resolution صريحة.
4. **الأسرار لا تحفظ كنص**: invitation tokens وaccess tokens تحفظ فقط كـSHA-256 digests وتظهر للمستخدم مرة واحدة عند الإصدار.
5. **Transport fail-closed**: HTTP مسموح على loopback فقط؛ أي bind بعيد يحتاج TLS cert/key، والـclient يرفض remote plaintext HTTP.
6. **Audit قابل للتحقق**: أحداث الفريق في append-only SHA-256 hash chain.
7. **Cloud اختياري لاحقًا**: يمكن لاحقًا تقديم W1-hosted service تستخدم نفس العقود، لكن لا تصبح تبعية للنواة.

## نموذج الفريق

الأدوار: `owner`, `admin`, `member`, `viewer`.

- owner: السلطة العليا، بما في ذلك منح/سحب owner.
- admin: إدارة الدعوات والمشاريع، لكن لا يستطيع خفض owner.
- member: إنشاء مشاريع والعمل في المشاريع الممنوحة له.
- viewer: عضوية قراءة؛ الوصول الفعلي للمشروع يحتاج ProjectShare.

الدعوات one-time، محددة الصلاحية، ولا تخزن W1 token الخام. قبول الدعوة يضيف العضوية مرة واحدة ويغلق الدعوة.

## مشاركة المشروع

مستويات الوصول: `viewer`, `editor`, `manager`.

- viewer: state/pull.
- editor: viewer + push mutations.
- manager: editor + grant/revoke project shares.

Team admins/owners يحصلون على manager access إداريًا لجميع مشاريع الفريق، بينما بقية الأعضاء يرون فقط المشاريع التي شاركت معهم.

## Replica sync

كل جهاز مسجل كـReplica له:

- `device_id`
- principal/team binding
- monotonic sequence
- accepted event-chain head

كل `SyncMutation` يحمل:

- mutation id
- project/device
- `sequence`
- `base_revision`
- key + `set|delete`
- JSON value
- previous accepted event hash
- canonical SHA-256 event hash

يتحقق الخادم من التسلسل، chain head، hash، ACL، وحجم value (1 MB لكل mutation). إعادة نفس mutation id بنفس hash idempotent. إعادة نفس id بمحتوى مختلف fail-closed.

## التعارضات

إذا كان `base_revision` لا يساوي revision الحالية للمفتاح، لا يطبّق الخادم التعديل. ينشئ ConflictRecord يحتفظ بـ:

- client base revision
- client event hash
- العملية والقيمة المقترحة
- server revision + server event hash

ثم يجب تقديم mutation جديدة مبنية على revision الحالية إلى `resolve_conflict`. العملية المرفوضة لا تقدّم sequence الخاصة بالReplica، لذلك لا توجد فجوة مصطنعة في السلسلة المقبولة.

هذه ليست CRDT/OT rich-text co-editing. اختيار التعارض الصريح مقصود لأنه يحافظ على الحوكمة عند المعاني المهمة بدل دمج غير مرئي.

## Self-hosted API

السطح الحالي:

- `GET /v1/health`
- `GET /v1/projects`
- `GET /v1/state?project_id=...`
- `GET /v1/sync/pull?project_id=...&after=...`
- `POST /v1/sync/push`
- `GET /v1/conflicts?project_id=...`

المصادقة Bearer access token scoped إلى Team/Principal، بمدة صلاحية محددة (1 ساعة إلى 365 يومًا) وقابل للإلغاء. قاعدة البيانات تخزن digest فقط.

يمكن للتطبيقات استخدام `CollaborationClient` و`SyncMutation` من `w1cip.sdk` من دون استيراد الوحدات الداخلية.

## CLI

```text
w1 collab benchmark
w1 collab team create|list|show|members|set-role|remove-member
w1 collab invite create|accept|revoke
w1 collab project create|list|state
w1 collab share grant|revoke
w1 collab token issue|revoke
w1 collab replica register
w1 collab sync push|pull
w1 collab conflicts list|resolve
w1 collab audit list|verify
w1 collab serve
```

`w1 collaboration` alias مقبول أيضًا.

## الحدود الحالية

- لا W1-hosted cloud production service حتى الآن.
- لا relay/NAT traversal أو multi-region managed control plane.
- TLS transport يحمي القناة، لكن لا يوجد E2EE للمحتوى بمفاتيح أجهزة asymmetric أو external KMS بعد.
- لا CRDT/OT cursors لتحرير rich-text لحظيًا.
- المزامنة الحالية هي replicated JSON project state/event stream وليست مرآة ملفات filesystem عامة.
- لا managed backup/disaster-recovery أو billing/hosted tenant provisioning.

هذه الحدود مقصودة حتى تبقى Step 40 قابلة للتشغيل والقياس Self-hosted قبل بناء Cloud اختياري.
