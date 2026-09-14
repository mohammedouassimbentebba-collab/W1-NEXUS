# مثال Multi-Model Access Fabric

انسخ `model-access.example.json` إلى:

```text
.w1nexus/model-access.json
```

ثم:

```bash
w1 --workspace . models sync
w1 --workspace . models list
w1 --workspace . models benchmark
w1 --workspace . evaluate
```

لا تشغل المثال السحابي قبل استبدال معرفات النماذج وإعداد متغيرات الأسرار الخاصة بك.
