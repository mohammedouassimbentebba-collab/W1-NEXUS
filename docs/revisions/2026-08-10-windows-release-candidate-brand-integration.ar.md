# Step 45 — Windows Release Candidate & Native Brand Integration

أغلقت هذه الخطوة فجوة الأصول الأصلية للتطبيق دون إعادة تصميم الشعار المختار. جرى اشتقاق SVG path وPNG شفاف وICO وICNS وfavicon وsplash بصورة deterministic من Master Concept المثبت بالـSHA-256.

أصبح PyInstaller يستخدم `brand/production/w1-nexus.ico` ويضمّن Windows version resource، ويستخدم Inno Setup نفس الأيقونة مع per-user HKCU associations. أضيف `w1 packaging rc-check --source-root .` ليثبت أن الأصول hashes صحيحة وأن ملفات packaging المحفوظة تطابق المولد deterministic.

هذه الخطوة لا تدعي أن EXE أو Installer قد جرى تجميعه أو توقيعه Authenticode على مضيف Windows؛ المصدر RC-ready، أما public native release فيحتاج Windows build حيًا وهوية توقيع فعلية.
