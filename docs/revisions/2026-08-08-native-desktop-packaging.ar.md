# مذكرة Step 38 — Native Desktop Packaging

**الإصدار:** `0.1.0.dev38`

أضيفت طبقة `native_packaging.py` مع هوية تطبيق مستقرة، parser fail-closed لـ`w1://`، descriptor نسبي `.w1nexus`، NativeServiceController مرتبط بهوية العملية، UpdateManifest يتحقق من HTTPS/size/SHA-256 ويُبقي native signature verification حدًا مستقلًا، ومولد deterministic لمصادر Windows PyInstaller/Inno Setup مع HKCU associations وsigning hooks.

أضيف `native_entrypoint.py` ليكون مدخل EXE المحتمل، وCLI `w1 packaging ...`، ومصادر بناء Windows داخل `packaging/windows`، وworkflow Windows يبني artifact غير موقع وموسوم `UNSIGNED-NOT-FOR-PUBLICATION`.

القرار الأمني: لا deep link أو project descriptor يتحول إلى shell command، لا signing private keys في repository، ولا يمكن تسمية artifact موقّعًا قبل تحقق native signing فعلي.
