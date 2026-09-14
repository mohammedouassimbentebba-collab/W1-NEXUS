# Step 42 — AI Connections & Multi-Model Team Studio

توحّد هذه الخطوة Credential Broker وModel Access وDesktop Shell في تجربة اتصال واحدة. أضيف provider catalog، Connections متعددة، Model onboarding، AI Team definitions، وstrategy جديدة `challenge_synthesis` تمرر مخرجات النماذج فعليًا من المنتجين إلى challenger ثم إلى synthesizer.

أهم قرار أمني: Connections لا تخزن raw credentials، كما لا يسمح لمزود built-in بإعادة توجيه credential إلى hostname مختلف. Gemini OAuth يستخدم Bearer connector منفصلًا بدل معاملته كمفتاح API.

الـSDK العام أصبح `w1cip.sdk 1.1.0` بإضافات توافقية تشمل `AITeamDefinition` و`AITeamMember` و`AIConnectionService` و`provider_catalog`، مع بقاء Plugin API عند 1.0.
