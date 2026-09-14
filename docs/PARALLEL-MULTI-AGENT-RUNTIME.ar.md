# التشغيل المتوازي متعدد الوكلاء ومنسق الدمج

تضيف هذه الطبقة إلى W1 Nexus قدرة تنفيذ عدة وكلاء في الوقت نفسه داخل مشروع Git واحد، من دون السماح لهم بالكتابة في شجرة العمل نفسها أو دمج أعمالهم تلقائيًا.

## مبادئ التصميم

1. **عزل الكتابة:** لكل وكيل فرع وGit worktree مستقلان.
2. **الاعتماديات قبل التوازي:** لا تبدأ المهمة حتى تكتمل المهام التي تعتمد عليها.
3. **أقفال الموارد المشتركة:** المسارات المعلنة المتداخلة تُنفّذ تسلسليًا حتى لو كانت الفروع منفصلة.
4. **الفشل لا يختفي:** فشل مهمة يحجب المهام التابعة لها، ويمكن لسياسة `fail_fast` إيقاف البقية.
5. **لا دمج ذاتي:** اكتمال جميع الفروع ينتج `MergeProposal` فقط.
6. **مراجعة مستقلة:** لا يستطيع أي وكيل منتج اعتماد الدمج.
7. **اختبارات قبل الدمج:** يبنى Integration Worktree وتُدمج الفروع داخله ثم تُشغّل اختبارات محددة.
8. **تثبيت الهدف:** إذا تقدم الفرع الهدف بعد إنشاء المقترح، يرفض التطبيق ويجب إنشاء مقترح جديد.

## خطة الوكلاء

```json
{
  "run_id": "feature-auth-parallel",
  "target_branch": "main",
  "policy": {
    "max_workers": 3,
    "fail_fast": false,
    "lock_wait_seconds": 300,
    "require_clean_repository": true
  },
  "jobs": [
    {
      "job_id": "backend-auth",
      "agent_id": "backend-agent",
      "branch": "w1/backend-auth",
      "argv": ["python", "scripts/build_backend.py"],
      "resource_paths": ["src/backend/auth"],
      "expected_outputs": ["src/backend/auth/service.py"]
    },
    {
      "job_id": "frontend-auth",
      "agent_id": "frontend-agent",
      "branch": "w1/frontend-auth",
      "argv": ["python", "scripts/build_frontend.py"],
      "resource_paths": ["src/frontend/auth"],
      "expected_outputs": ["src/frontend/auth/Login.tsx"]
    },
    {
      "job_id": "integration-tests",
      "agent_id": "test-agent",
      "branch": "w1/auth-tests",
      "argv": ["python", "scripts/add_auth_tests.py"],
      "dependencies": ["backend-auth", "frontend-auth"],
      "resource_paths": ["tests/auth"],
      "expected_outputs": ["tests/auth/test_login.py"]
    }
  ]
}
```

## التشغيل

```bash
w1 --workspace ./project agents run \
  --plan parallel-plan.json \
  --approved-by human-owner

w1 --workspace ./project agents status feature-auth-parallel
```

الموافقة على التشغيل لا تعني الموافقة على الدمج؛ إنها تسمح بإنشاء worktrees وتنفيذ الأوامر المعلنة فقط.

## Event Bus والسجل

كل تشغيل يحفظ في SQLite:

- حالة التشغيل.
- مواصفات المهام.
- حالة كل وكيل.
- مسار worktree والفرع والـcommit.
- الأقفال النشطة.
- أحداث البدء والنهاية والحجب والإلغاء.
- مقترحات الدمج ومراجعاتها.

كما يمكن إضافة مستمعين داخل العملية إلى `ParallelAgentRuntime.subscribe()` لعرض التقدم حيًا في الواجهة الرسومية مستقبلًا.

## أقفال الموارد

Worktrees تمنع الكتابة الفعلية في المجلد نفسه، لكنها لا تمنع وكيلين من تعديل الملف المنطقي نفسه على فرعين مختلفين. لذلك تعلن المهمة الموارد التي تنوي تعديلها:

```json
"resource_paths": ["src/payments", "schemas/invoice.json"]
```

يتعارض:

```text
src/payments
src/payments/refunds.py
```

ولا يتعارض:

```text
src/payments
src/profile
```

الأقفال **استشارية داخل W1**، وليست قفلًا لنظام الملفات ضد برامج خارجية.

## بناء Merge Proposal

```bash
w1 --workspace ./project merge propose feature-auth-parallel \
  --test-argv-json '["python","-m","pytest","-q"]'
```

ينشئ المنسق:

1. فرع Integration جديد من `base_commit` المثبت.
2. Worktree مستقلة للدمج.
3. دمجًا متسلسلًا لفروع الوكلاء.
4. كشف تعارضات Git الفعلية.
5. قائمة مبكرة للملفات التي عدلها أكثر من وكيل.
6. تشغيل الاختبارات المحددة.
7. `integration_commit` لا يمس الفرع الهدف بعد.

الحالات:

```text
proposed
conflicted
tests_failed
tests_passed
```

## المراجعة المستقلة

```bash
w1 --workspace ./project merge review <proposal-id> \
  --reviewer independent-reviewer \
  --outcome approved \
  --rationale "Diff and tests accepted"
```

لا تقبل `approved` إلا إذا:

- لا توجد تعارضات Git.
- نجحت الاختبارات.
- المراجع ليس أحد الوكلاء المنتجين.
- بصمة المقترح لم تتغير بعد المراجعة.

## تطبيق الدمج

```bash
w1 --workspace ./project merge apply <proposal-id> \
  --approved-by human-owner
```

التطبيق يستخدم `git merge --ff-only` عبر `ActionRuntime` وموافقة مرتبطة بالفعل. إذا تقدم الفرع الهدف منذ بداية التشغيل، يرفض الدمج بدل الكتابة فوق عمل جديد.

## الإلغاء والمهلات

- لكل مهمة مهلة مستقلة.
- `cancel(run_id)` يوقف العمليات الجارية ويلغي ما لم يبدأ.
- المهام التابعة لفشل أو إلغاء تصبح `blocked` أو `cancelled` حسب الحالة.
- مخرجات العمليات محدودة الحجم، وتزال المتغيرات السرية من بيئة الوكيل.

## الحدود الحالية

هذه الخطوة تنفيذ محلي قوي، لكنها ليست بعد النظام النهائي الكامل:

- التنفيذ يعتمد Threads داخل جهاز واحد، وليس موزعًا بين عدة خوادم.
- لا يوجد استئناف تلقائي لمهمة كانت تعمل لحظة انهيار العملية؛ السجل يحفظ حالتها، لكن تحتاج سياسة reconciliation لاحقة.
- الأقفال استشارية ولا تمنع برامج خارج W1.
- اكتشاف التعارض الدلالي يعتمد حاليًا على Git والاختبارات والمسارات المشتركة؛ مراجعة AI دلالية للـdiff ستضاف لاحقًا.
- أوامر الوكلاء ليست معزولة عن النواة عبر Container/VM بعد؛ لا يُشغّل كود عدائي دون Sandbox نظامي.
- تنظيف worktrees والفروع القديمة ما يزال عملية إدارة مستقلة.

## موقعها في W1 Nexus الكاملة

هذه الطبقة هي أساس:

- لوحة وكلاء تعمل بالتوازي في Workspace.
- فرق برمجة وبحث وتصميم متعددة التخصصات.
- بث تقدم حي وإلغاء فردي.
- مقارنة عدة حلول للمهمة نفسها.
- مراجعة ودمج محكومان.
- توزيع لاحق على خوادم وحاويات متعددة.
