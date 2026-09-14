# W1 Native Desktop Shell + Artifact Studio Foundation

تضيف الخطوة 33 سطح عمل محليًا أولًا فوق نواة W1 Nexus. الغرض ليس بناء محرر منفصل يتجاوز الحوكمة، بل منح المستخدم واجهة موحدة لقراءة المشروع، إعداد نسخ العمل، مراجعتها، ثم نشرها عبر `Action Runtime`.

## حدود الادعاء

الإصدار الحالي يوفر واجهة PWA محلية قابلة للتثبيت في المتصفح، ويمكن فتحها في نافذة نظام أصلية عندما تكون مكتبة `pywebview` مثبتة. لا يتضمن الإصدار بعد حزم MSI أو DMG أو AppImage موقعة، ولذلك يسمى **Desktop Shell Foundation** ولا يدعي اكتمال تطبيق سطح المكتب التجاري.

## المبادئ

- يعمل دون خوادم مملوكة لـW1.
- يرتبط افتراضيًا بـ`127.0.0.1` فقط.
- لا يحمل مكتبات أو خطوطًا أو موارد من CDN.
- القراءة هي الوضع الافتراضي؛ العمليات تحتاج `--allow-operations`.
- تعديل المحرر ينشئ نسخة Artifact غير قابلة لإعادة الكتابة فوق نسخة سابقة.
- النشر إلى Workspace يمر عبر مراجعة مستقلة و`Action Runtime`.
- لا يستطيع المؤلف اعتماد نسخته بنفسه.
- يمنع النشر إذا تغير الملف الأصلي بعد إنشاء المسودة.

## المكونات

### Project Explorer

يعرض ملفات المشروع مع استبعاد `.git` و`.w1nexus`، ويرفض المسارات المطلقة و`..` والروابط الرمزية. القراءة محدودة بالحجم ولا تتبع مسارًا خارج جذر المشروع.

### Code and Text Editor

يوفر محررًا محليًا للنصوص والكود مع أرقام الأسطر. الحفظ لا يكتب الملف مباشرة، بل ينشئ `ArtifactVersion` جديدة تحتوي على:

- بصمة المحتوى.
- بصمة النسخة الأب.
- بصمة الملف الذي بدأت منه المسودة.
- المؤلف والتوقيت والحالة.

### Safe Preview

يدعم حاليًا:

- JSON مع التحقق والبنية المنظمة.
- CSV كجدول محدود الصفوف.
- Markdown والكود والنصوص.
- Metadata محلية لـPDF والصور والصوت والفيديو والملفات الثنائية.

لا توجد في هذه الخطوة محركات تحرير غنية لـDOCX أو XLSX أو PPTX، ولا محرر PDF للتعليقات.

### Review and Publication

التسلسل الإلزامي:

```text
Draft
→ Immutable version
→ Independent review
→ Workspace conflict check
→ Action Runtime approval/policy
→ Atomic publication
```

بعد النشر تسجل قاعدة Artifact Studio:

- Action ID.
- Result status.
- بصمة الملف المنشور.
- الطرف الذي نشره.
- سجل أحداث متسلسل بالبصمات.

### Governed Terminal

الطرفية ليست Shell حرًا. تبني طلب `command.run` وتعرض خطة السياسة أولًا. التنفيذ يمر عبر Action Runtime بقائمة `argv` صريحة، وحدود الوقت والخرج، وموافقة عندما تتطلب السياسة ذلك.

### Git and Model Portfolios

تعرض الواجهة Git diff من المشروع الحقيقي، وتقرأ ملفات النماذج وPortfolios متعددة النماذج من `Multi-Model Access Fabric`. لا تختزل W1 العمل في نموذج واحد؛ يستطيع المستخدم اختيار فريق نماذج محلية وسحابية وخاصة وتطبيقات خارجية.

## أوامر CLI

```bash
w1 --workspace ./project studio tree
w1 --workspace ./project studio open README.md
w1 --workspace ./project studio preview data.json
w1 --workspace ./project studio git-diff

w1 --workspace ./project studio artifacts create \
  --artifact-id readme-update --path README.md --created-by author
w1 --workspace ./project studio artifacts save readme-update \
  --content-file draft.md --created-by author --expected-parent-hash HASH
w1 --workspace ./project studio artifacts review readme-update \
  --version 2 --reviewer reviewer --outcome approved --rationale "Verified"
w1 --workspace ./project studio artifacts publish readme-update \
  --published-by owner --approve-action

w1 --workspace ./project studio terminal plan \
  --action-id test-project --argv-json '["python","-m","pytest","-q"]'
w1 --workspace ./project studio terminal run \
  --action-id test-project --argv-json '["python","-m","pytest","-q"]' --approve
```

## تشغيل الواجهة

```bash
w1 --workspace ./project desktop doctor
w1 --workspace ./project desktop launch
```

للعمليات المحلية:

```bash
w1 --workspace ./project desktop launch --allow-operations
```

أو تشغيل الخادم المحلي دون فتح الواجهة:

```bash
w1 --workspace ./project desktop serve --no-browser
```

## الحماية المحلية

- Bearer Token محفوظ في `.w1nexus/desktop-shell.token` بصلاحية `0600` حيث يدعم النظام ذلك.
- حماية Host من DNS rebinding.
- CSP مقيدة وCORS غير مفعل.
- رفض الربط بعنوان عام.
- لا تسرب لقيم أسرار النماذج إلى حالة الواجهة.
- لا وصول مباشر إلى حالة W1 الداخلية عبر مستكشف الملفات.

## التبني داخل منتجات أخرى

يمكن لشركة ذكاء اصطناعي استخدام Artifact Store وFile Service مباشرة عبر Python، أو تضمين الواجهة المحلية، أو بناء واجهتها الخاصة فوق W1 SDK وLocal Control API. لا توجد تبعية إلى خادم W1 مركزي أو إلى نموذج بعينه.

## المتبقي

- حزم سطح مكتب أصلية وموقعة لأنظمة التشغيل.
- محرر كود متقدم بخادم لغة وتصحيح أخطاء.
- محركات DOCX/XLSX/PPTX وPDF غنية.
- معاينة فيديو وصوت متقدمة.
- مزامنة تعاونية اختيارية.
- Computer Use محكوم.
