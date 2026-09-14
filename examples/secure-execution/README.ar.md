# مثال Secure Execution Fabric

بعد تثبيت W1:

```bash
w1 --workspace ./project init
w1 --workspace ./project sandboxes doctor
w1 --workspace ./project sandboxes profiles add --file profile.local.json
w1 --workspace ./project sandboxes plan --request request.local.json
w1 --workspace ./project sandboxes run --request request.local.json
w1 --workspace ./project sandboxes verify-attestation secure-demo-local
```

لتجربة OCI أضف `profile.oci.json`. يحتاج التشغيل إلى Docker أو Podman صالح. إذا لم يوجد محرك، يرفض W1 التنفيذ بدل الرجوع إلى Local.

يمكن استعمال `sandbox_profile` داخل كل عنصر في `parallel-plan.example.json` لتشغيل وكلاء متوازيين بحدود منفصلة.
