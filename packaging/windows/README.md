# W1 Nexus — Windows native packaging

هذه الملفات هي **مصادر بناء** Windows الخاصة بـStep 38، وليست دليلاً بحد ذاتها على أن ملفًا تنفيذيًا أو Installer موقّعًا قد بُني على هذا المضيف.

- `w1-nexus.spec`: مدخل PyInstaller للتطبيق الرسومي.
- `installer.iss`: Inno Setup per-user installer؛ يسجل `w1://` و`.w1nexus` تحت HKCU ولا يحتاج صلاحيات Administrator افتراضيًا.
- `register-user.ps1` / `unregister-user.ps1`: تسجيل/إزالة الارتباطات للمستخدم الحالي.
- `build.ps1`: بناء Windows محلي اختياري. إذا كان `W1_WINDOWS_SIGN_CERT_SHA1` مضبوطًا على شهادة موجودة أصلًا في Windows certificate store، يوقّع EXE والـInstaller ثم يتحقق من Authenticode. بدون شهادة تبقى الملفات **غير موثوقة للنشر العام**.
- `packaging-manifest.json`: hashes ومحددات البناء المولدة آليًا.

GitHub Actions workflow الموجود في `.github/workflows/native-windows-package.yml` يبني artifact **غير موقّع وموسوم بوضوح بأنه ليس للنشر العام**، حتى تتوفر هوية توقيع فعلية.

لا توضع مفاتيح API أو OAuth tokens أو private signing keys في هذه الملفات أو في command-line arguments.
