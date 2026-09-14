# تجربة W1 Nexus Workspace

## تشغيل واجهة فارغة

```bash
python -m pip install ../../w1_nexus-0.1.0.dev32-py3-none-any.whl
mkdir workspace-demo
cd workspace-demo
w1 init
w1 workspace serve
```

## تعبئة الواجهة بالدورة المرجعية

في طرفية ثانية:

```bash
w1 --workspace ./workspace-demo demo --reset --events silent
```

ثم حدّث الصفحة أو انتظر البث الحي.

## تصدير Snapshot

```bash
w1 --workspace ./workspace-demo workspace snapshot \
  --output ./workspace-demo/.w1nexus/reports/workspace.json
```

## ملاحظة أمنية

لا تستعمل `--allow-operations` إلا على جهازك المحلي. الخادم يرفض أصلًا الاستماع على عنوان عام.
