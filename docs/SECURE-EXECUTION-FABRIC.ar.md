# W1 Secure Execution Fabric

توفر هذه الطبقة حدود تنفيذ قابلة للإعلان والتدقيق لوكلاء W1 Nexus وأدواته. وهي تفصل بين تشغيل محلي محدود للأعمال الموثوقة وبين عزل صلب مبني على Docker أو Podman عندما يكون أحدهما متاحًا.

## الهدف

لا يكفي أن يقرر W1 أن أمرًا مسموح؛ يجب أن يحدد أيضًا البيئة التي سينفذ فيها، الموارد المتاحة، الشبكة، الملفات القابلة للقراءة والكتابة، الأسرار، والمواد التي يسمح بإخراجها بعد التنفيذ.

كل تنفيذ يرتبط بـ`SandboxProfile` ثابتة، و`SandboxRequest`، وسجل SQLite، ونتيجة تحمل Telemetry وArtifacts وAttestation محلية.

## فئات التنفيذ

### OCI: Docker أو Podman

هذا هو المسار الموصى به للكود غير الموثوق أو للوكلاء الذين ينفذون أدوات عامة. يطبق W1:

- جذر حاوية للقراءة فقط عند طلبه.
- Workspace بتركيب `ro` أو `rw` صريح.
- `no-new-privileges`.
- إسقاط Linux capabilities.
- مستخدم غير root؛ افتراضيًا UID/GID المستخدم المضيف.
- حد للذاكرة ومنع swap الزائد.
- حد لأنوية CPU.
- حد لعدد العمليات والملفات المفتوحة وحجم الملفات.
- `tmpfs` مؤقت لـ`/tmp`.
- شبكة `none` افتراضيًا.
- مهلة جدارية مع قتل إجباري للحاوية.

إذا طلبت Profile عزلًا صلبًا ولم يوجد Docker أو Podman، يتوقف التنفيذ بخطأ `sandbox_backend_unavailable`. لا يسمح بالهبوط الصامت إلى تشغيل محلي.

### Local

المسار المحلي مخصص للكود الموثوق أو شبه الموثوق. على POSIX يطبق مشغل فرعي مستقل:

- `RLIMIT_CPU`.
- `RLIMIT_AS` للذاكرة حيث يدعم النظام ذلك.
- `RLIMIT_NPROC`.
- `RLIMIT_NOFILE`.
- `RLIMIT_FSIZE`.
- مجموعة عملية مستقلة وقتل عند المهلة أو الإلغاء.

لكن Local ليس namespace للملفات أو الشبكة. لذلك يرفض W1 Profile محلية تطلب `network: none` أو `require_hard_isolation: true` بدل الادعاء بأنه طبق حماية غير موجودة.

## SandboxProfile

مثال OCI:

```json
{
  "profile_id": "python-agent-hardened",
  "backend": "auto",
  "image": "python:3.13-slim",
  "root_read_only": true,
  "workspace_mode": "rw",
  "run_as_user": "host",
  "network": {"mode": "none"},
  "limits": {
    "wall_seconds": 300,
    "cpu_seconds": 120,
    "cpu_cores": 1.0,
    "memory_mb": 1024,
    "pids": 128,
    "open_files": 512,
    "file_size_mb": 256,
    "output_bytes": 1000000,
    "tmpfs_mb": 128,
    "artifact_bytes": 100000000
  },
  "allowed_commands": ["python", "python3", "pytest"],
  "artifact_globs": ["artifacts/**", "reports/**"],
  "mount_source_roots": ["."],
  "secret_names": ["MODEL_TOKEN"],
  "require_hard_isolation": true
}
```

`mount_source_roots` تحدد الأصول التي يمكن تركيبها. لا يستطيع طلب صادر من وكيل تركيب `/etc` أو مجلد مستخدم آخر ما لم تسمح Profile بذلك صراحة.

## الأسرار

ملف الطلب لا يحمل قيمة السر. يحمل مرجع متغير البيئة فقط:

```json
{
  "secret_env": {
    "MODEL_TOKEN": "W1_MODEL_TOKEN"
  }
}
```

تحل CLI المرجع وقت التنفيذ. في OCI تكتب القيم إلى ملف بيئة مؤقت بصلاحية `0600`، يقرأه محرك الحاوية، ثم يحذف المجلد المؤقت. في Local تدخل القيم إلى بيئة العملية الفرعية فقط.

لا تحفظ طبقة التحكم في W1 القيم في:

- SandboxRequest المسجلة.
- SQLite.
- Attestation.
- CLI output.
- ملفات Profile.

ينقح W1 القيم السرية المعروفة من stdout وstderr قبل التسجيل. لكنه لا يدعي منع برنامج خبيث مخول بالسر من كتابته في ملف عادي داخل Workspace قابلة للكتابة؛ لهذا تستخدم Profiles شديدة الحساسية Workspace للقراءة فقط أو وسيط أسرار محدود الوظيفة.

## الشبكة

الأنماط:

- `none`: لا شبكة خارج namespace في OCI.
- `loopback`: شبكة خارجية معطلة، ويبقى loopback داخل البيئة.
- `inherit`: شبكة المضيف؛ لا يسمح بها Local إلا بصورة صريحة.
- `allowlist`: يمنع W1 الاتصال المباشر؛ يلزم Proxy أو Firewall خارجي لتطبيق قائمة أسماء مضيفين. لم ينفذ Proxy الداخلي بعد.

## Telemetry

تسجل النتيجة:

- الزمن الجداري.
- زمن CPU للمستخدم والنظام حيث يتوفر.
- Peak RSS تقريبي في Local.
- أحجام stdout وstderr الفعلية.
- كون الخرج قد قُطع.
- نوع التنفيذ المستخدم لكل حد.

يقرأ W1 الخرج أثناء التشغيل ويحتفظ بحد أقصى معلوم في الذاكرة، بدل انتظار انتهاء العملية مع خرج غير محدود.

## Artifacts

بعد التنفيذ يبحث W1 داخل Workspace فقط عن الأنماط المعلنة. لا يتبع الروابط الرمزية، ويطبق حدًا إجماليًا للحجم، ثم ينسخ الملفات إلى:

```text
.w1nexus/sandbox-artifacts/<execution-id>/
```

كل Artifact تسجل:

- المسار النسبي.
- الحجم.
- SHA-256.
- المسار المحفوظ.

## Attestation

تربط Attestation المحلية:

- بصمة الطلب المنقح.
- بصمة Profile.
- بصمة النتيجة.
- بصمة قائمة Artifacts.
- Backend.
- حدود التنفيذ.
- وضع الشبكة.
- أسماء الأسرار دون قيمها.

توقع بـHMAC محلي. وهي تثبت ما لاحظه تثبيت W1 نفسه، وليست Attestation عتادية أو توقيعًا بمفتاح مؤسسة خارجي.

## Idempotency والتعافي

إعادة `execution_id` نفسها مع الطلب نفسه تعيد النتيجة المسجلة ولا تعيد تشغيل الأمر. استعمال المعرف نفسه بطلب مختلف يرفض.

إذا انهار المضيف بعد بدء عملية وقبل تثبيت النتيجة، تبقى الحالة بحاجة إلى reconciliation ولا يعيد W1 التنفيذ تلقائيًا، لتجنب تكرار أثر خارجي غير معلوم.

## التكامل مع Parallel Agent Runtime

يمكن لكل `AgentJob` إعلان:

```json
{
  "sandbox_profile": "python-agent-hardened",
  "sandbox_artifact_globs": ["reports/**"],
  "secret_env": {
    "MODEL_TOKEN": "W1_MODEL_TOKEN"
  }
}
```

الأعمال بلا `sandbox_profile` تستمر في المسار القديم المتوافق. الأعمال التي تعلن Profile تمر عبر Secure Execution Fabric، ولا تغير Profile أو Backend بصمت.

## أوامر CLI

```bash
w1 sandboxes doctor
w1 sandboxes profiles list
w1 sandboxes profiles add --file profile.json
w1 sandboxes plan --request request.json
w1 sandboxes run --request request.json
w1 sandboxes status execution-id
w1 sandboxes list
w1 sandboxes verify-attestation execution-id
```

## حدود هذه الخطوة

لم ينفذ بعد:

- MicroVM مثل Firecracker.
- Windows Sandbox أو Hyper-V backend.
- Kubernetes jobs.
- Proxy داخلي لقوائم الشبكة.
- eBPF telemetry.
- توقيع Attestation بمفتاح HSM أو KMS.
- قياس صورة الحاوية أو TEE attestation.
- نقل Sandboxes إلى خوادم بعيدة.

هذه الحدود لا تمنع الانتقال إلى W1 Nexus الكاملة؛ بل تحدد طبقة العزل الحالية بدقة.
